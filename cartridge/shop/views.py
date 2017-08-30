from __future__ import unicode_literals

from json import dumps

from cartridge.shop import checkout
from cartridge.shop.forms import (AddProductForm, CartItemFormSet,
                                  DiscountForm, OrderForm)
from cartridge.shop.models import DiscountCode
from cartridge.shop.models import Product, ProductVariation, Order
from cartridge.shop.utils import recalculate_cart, sign
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import User
from django.contrib.messages import info, error
from django.core.urlresolvers import reverse
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template import RequestContext
from django.template.defaultfilters import slugify
from django.template.loader import get_template
from django.template.response import TemplateResponse
from django.utils.translation import ugettext as _
from django.views.decorators.cache import never_cache
from future.builtins import int, str
from mezzanine.accounts import get_profile_form
from mezzanine.conf import settings
from mezzanine.utils.importing import import_dotted_path
from mezzanine.utils.urls import next_url
from mezzanine.utils.views import set_cookie, paginate
from paypal.pro.exceptions import PayPalFailure
from paypal.pro.helpers import PayPalWPP, express_endpoint_for_token
from paypal.pro.views import PayPalPro
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import int_to_base36
from django.contrib.auth import authenticate


try:
    from xhtml2pdf import pisa
except (ImportError, SyntaxError):
    pisa = None
HAS_PDF = pisa is not None


# Set up checkout handlers.
handler = lambda s: import_dotted_path(s) if s else lambda *args: None
billship_handler = handler(settings.SHOP_HANDLER_BILLING_SHIPPING)
tax_handler = handler(settings.SHOP_HANDLER_TAX)
payment_handler = handler(settings.SHOP_HANDLER_PAYMENT)
order_handler = handler(settings.SHOP_HANDLER_ORDER)


def product(request, slug, template="shop/product.html",
            form_class=AddProductForm, extra_context=None):
    """
    Display a product - convert the product variations to JSON as well as
    handling adding the product to either the cart or the wishlist.
    """
    published_products = Product.objects.published(for_user=request.user)
    product = get_object_or_404(published_products, slug=slug)
    fields = [f.name for f in ProductVariation.option_fields()]
    variations = product.variations.all()
    variations_json = dumps([dict([(f, getattr(v, f))
        for f in fields + ["sku", "image_id"]]) for v in variations])
    to_cart = (request.method == "POST" and
               request.POST.get("add_wishlist") is None)
    initial_data = {}
    if variations:
        initial_data = dict([(f, getattr(variations[0], f)) for f in fields])
    initial_data["quantity"] = 1
    add_product_form = form_class(request.POST or None, product=product,
                                  initial=initial_data, to_cart=to_cart)
    if request.method == "POST":
        if add_product_form.is_valid():
            if to_cart:
                quantity = add_product_form.cleaned_data["quantity"]
                request.cart.add_item(add_product_form.variation, quantity)
                recalculate_cart(request)
                info(request, _("Item added to cart"))
                return redirect("shop_cart")
            else:
                skus = request.wishlist
                sku = add_product_form.variation.sku
                if sku not in skus:
                    skus.append(sku)
                info(request, _("Item added to wishlist"))
                response = redirect("shop_wishlist")
                set_cookie(response, "wishlist", ",".join(skus))
                return response
    related = []
    if settings.SHOP_USE_RELATED_PRODUCTS:
        related = product.related_products.published(for_user=request.user)
    context = {
        "product": product,
        "editable_obj": product,
        "images": product.images.all(),
        "variations": variations,
        "variations_json": variations_json,
        "has_available_variations": any([v.has_price() for v in variations]),
        "related_products": related,
        "add_product_form": add_product_form
    }
    context.update(extra_context or {})
    templates = [u"shop/%s.html" % str(product.slug), template]
    return TemplateResponse(request, templates, context)


@never_cache
def wishlist(request, template="shop/wishlist.html",
             form_class=AddProductForm, extra_context=None):
    """
    Display the wishlist and handle removing items from the wishlist and
    adding them to the cart.
    """

    if not settings.SHOP_USE_WISHLIST:
        raise Http404

    skus = request.wishlist
    error = None
    if request.method == "POST":
        to_cart = request.POST.get("add_cart")
        add_product_form = form_class(request.POST or None,
                                      to_cart=to_cart)
        if to_cart:
            if add_product_form.is_valid():
                request.cart.add_item(add_product_form.variation, 1)
                recalculate_cart(request)
                message = _("Item added to cart")
                url = "shop_cart"
            else:
                error = list(add_product_form.errors.values())[0]
        else:
            message = _("Item removed from wishlist")
            url = "shop_wishlist"
        sku = request.POST.get("sku")
        if sku in skus:
            skus.remove(sku)
        if not error:
            info(request, message)
            response = redirect(url)
            set_cookie(response, "wishlist", ",".join(skus))
            return response

    # Remove skus from the cookie that no longer exist.
    published_products = Product.objects.published(for_user=request.user)
    f = {"product__in": published_products, "sku__in": skus}
    wishlist = ProductVariation.objects.filter(**f).select_related("product")
    wishlist = sorted(wishlist, key=lambda v: skus.index(v.sku))
    context = {"wishlist_items": wishlist, "error": error}
    context.update(extra_context or {})
    response = TemplateResponse(request, template, context)
    if len(wishlist) < len(skus):
        skus = [variation.sku for variation in wishlist]
        set_cookie(response, "wishlist", ",".join(skus))
    return response


@never_cache
def cart(request, template="shop/cart.html",
         cart_formset_class=CartItemFormSet,
         discount_form_class=DiscountForm,
         extra_context=None):
    """
    Display cart and handle removing items from the cart.
    """
    cart_formset = cart_formset_class(instance=request.cart)
    discount_form = discount_form_class(request, request.POST or None)
    if request.method == "POST":
        valid = True
        if request.POST.get("update_cart"):
            valid = request.cart.has_items()
            if not valid:
                # Session timed out.
                info(request, _("Your cart has expired"))
            else:
                cart_formset = cart_formset_class(request.POST,
                                                  instance=request.cart)
                valid = cart_formset.is_valid()
                if valid:
                    cart_formset.save()
                    recalculate_cart(request)
                    info(request, _("Cart updated"))
                else:
                    # Reset the cart formset so that the cart
                    # always indicates the correct quantities.
                    # The user is shown their invalid quantity
                    # via the error message, which we need to
                    # copy over to the new formset here.
                    errors = cart_formset._errors
                    cart_formset = cart_formset_class(instance=request.cart)
                    cart_formset._errors = errors
        else:
            valid = discount_form.is_valid()
            if valid:
                discount_form.set_discount()
            # Potentially need to set shipping if a discount code
            # was previously entered with free shipping, and then
            # another was entered (replacing the old) without
            # free shipping, *and* the user has already progressed
            # to the final checkout step, which they'd go straight
            # to when returning to checkout, bypassing billing and
            # shipping details step where shipping is normally set.
            recalculate_cart(request)
        if valid:
            return redirect("shop_cart")
    context = {"cart_formset": cart_formset}
    context.update(extra_context or {})
    settings.use_editable()
    if (settings.SHOP_DISCOUNT_FIELD_IN_CART and
            DiscountCode.objects.active().exists()):
        context["discount_form"] = discount_form
    return TemplateResponse(request, template, context)


@never_cache
def checkout_steps(request, form_class=OrderForm, extra_context=None):
    """
    Display the order form and handle processing of each step.
    """
    # Do the authentication check here rather than using standard
    # login_required decorator. This means we can check for a custom
    # LOGIN_URL and fall back to our own login view.
    authenticated = request.user.is_authenticated()
    if settings.SHOP_CHECKOUT_ACCOUNT_REQUIRED and not authenticated:
        url = "%s?next=%s" % (settings.LOGIN_URL, reverse("shop_checkout"))
        return redirect(url)

    try:
        settings.SHOP_CHECKOUT_FORM_CLASS
    except AttributeError:
        pass
    else:
        from warnings import warn
        warn("The SHOP_CHECKOUT_FORM_CLASS setting is deprecated - please "
             "define your own urlpattern for the checkout_steps view, "
             "passing in your own form_class argument.")
        form_class = import_dotted_path(settings.SHOP_CHECKOUT_FORM_CLASS)

    initial = checkout.initial_order_data(request, form_class)
    _shipping= request.cart.need_to_ship()
    step = int(request.POST.get("step", None) or
               initial.get("step", None) or
               checkout.CHECKOUT_STEP_FIRST)
    form = form_class(request, step, initial=initial,shipping=_shipping)
    data = request.POST
    checkout_errors = []
    
    if request.POST.get("back") is not None:
        # Back button in the form was pressed - load the order form
        # for the previous step and maintain the field values entered.
        step -= 1
        form = form_class(request, step, initial=initial,shipping=_shipping)
    elif request.method == "POST" and request.cart.has_items():
        
        form = form_class(request, step, initial=initial, data=data,shipping=_shipping)
        if form.is_valid():
            # Copy the current form fields to the session so that
            # they're maintained if the customer leaves the checkout
            # process, but remove sensitive fields from the session
            # such as the credit card fields so that they're never
            # stored anywhere.
            
            request.session["order"] = dict(form.cleaned_data)
            
            sensitive_card_fields = ("card_number", "card_expiry_month",
                                     "card_expiry_year", "card_ccv")
            for field in sensitive_card_fields:
                if field in request.session["order"]:
                    del request.session["order"][field]
            
            # FIRST CHECKOUT STEP - handle discount code. This needs to
            # be set before shipping, to allow for free shipping to be
            # first set by a discount code.
            if step == checkout.CHECKOUT_STEP_FIRST:
                form.set_discount()

            # ALL STEPS - run billing/tax handlers. These are run on
            # all steps, since all fields (such as address fields) are
            # posted on each step, even as hidden inputs when not
            # visible in the current step.
            try:
                billship_handler(request, form)
                tax_handler(request, form)
            except checkout.CheckoutError as e:
                checkout_errors.append(e)
            
            # FINAL CHECKOUT STEP - run payment handler and process order.
            if step == checkout.CHECKOUT_STEP_LAST and not checkout_errors:
                
                # Create and save the initial order object so that
                # the payment handler has access to all of the order
                # fields. If there is a payment error then delete the
                # order, otherwise remove the cart items from stock
                # and send the order receipt email.
                order = form.save(commit=False)
                order.setup(request)
                # Try payment.
                try:
                    transaction_id = payment_handler(request, form, order)
                except checkout.CheckoutError as e:
                    # Error in payment handler.
                    order.delete()
                    checkout_errors.append(e)
                    if settings.SHOP_CHECKOUT_STEPS_CONFIRMATION:
                        step -= 1
                else:
                    # Finalize order - ``order.complete()`` performs
                    # final cleanup of session and cart.
                    # ``order_handler()`` can be defined by the
                    # developer to implement custom order processing.
                    # Then send the order email to the customer.
                    order.transaction_id = transaction_id
                    order.complete(request)
                    request.session['user']=order_handler(request, form, order)
                    checkout.send_order_email(request, order)
                    # Set the cookie for remembering address details
                    # if the "remember" checkbox was checked.
                    response = redirect("shop_complete")
                    if form.cleaned_data.get("remember"):
                        remembered = "%s:%s" % (sign(order.key), order.key)
                        set_cookie(response, "remember", remembered,
                                   secure=request.is_secure())
                    else:
                        response.delete_cookie("remember")
                    
                    return response

            # If any checkout errors, assign them to a new form and
            # re-run is_valid. If valid, then set form to the next step.
            form = form_class(request, step, initial=initial, data=data,
                              errors=checkout_errors)
            if form.is_valid():
                step += 1
                form = form_class(request, step, initial=initial,shipping=_shipping)

    # Update the step so that we don't rely on POST data to take us back to
    # the same point in the checkout process.
    try:
        request.session["order"]["step"] = step
        request.session.modified = True
    except KeyError:
        pass
    
    step_vars = checkout.CHECKOUT_STEPS[step - 1]
    template = "shop/%s.html" % step_vars["template"]
    context = {"CHECKOUT_STEP_FIRST": step == checkout.CHECKOUT_STEP_FIRST,
               "CHECKOUT_STEP_LAST": step == checkout.CHECKOUT_STEP_LAST,
               "CHECKOUT_STEP_PAYMENT": (settings.SHOP_PAYMENT_STEP_ENABLED and
                   step == checkout.CHECKOUT_STEP_PAYMENT),
               "step_title": step_vars["title"], "step_url": step_vars["url"],
               "steps": checkout.CHECKOUT_STEPS, "step": step, "form": form,
               'shipping':_shipping}
    context.update(extra_context or {})
    
    return TemplateResponse(request, template, context)

@never_cache
def express_cancel(request):
    try:
        del request.session['express_order']
    except:
        pass
    _cart=reverse('shop_cart')
    return HttpResponseRedirect(_cart)
    
@never_cache
def express_steps(request, form_class=OrderForm, extra_context=None):
    """
    SetExpressCheckout and handle the order
    """
    # Do the authentication check here rather than using standard
    # login_required decorator. This means we can check for a custom
    # LOGIN_URL and fall back to our own login view.
    authenticated = request.user.is_authenticated()
    if settings.SHOP_CHECKOUT_ACCOUNT_REQUIRED and not authenticated:
        url = "%s?next=%s" % (settings.LOGIN_URL, reverse("shop_express_checkout"))
        return redirect(url)
    #maybe need to modify this to not pull regular orders from db or session
    #perhaps we should get rid of this because well give paypal shipping details priority
    #and let them be changed on a per instance basis
    initial = checkout.initial_order_data(request, form_class,express=True)
    #this only posts during final step therefore will never get a post step variable
    step = int(request.POST.get("step", None) or
               initial.get("step", None) or 1)

    form = form_class(request, step, initial=initial)
    token = initial.get('token',None)
    total = initial.get('total',None)
    getdetails = initial.get('getdetails',True)
    wpp = PayPalWPP(request)
    
    checkout_errors = []
    # FIRST CHECKOUT STEP - handle discount code. This needs to
    # be set before shipping, to allow for free shipping to be
    # first set by a discount code.
    if step == checkout.EXPRESS_CHECKOUT_STEP_FIRST:
        form.set_discount()
 
    # ALL STEPS - run billing/tax handlers. These are run on
    # all steps, since all fields (such as address fields) are
    # posted on each step, even as hidden inputs when not
    # visible in the current step.
    try:
        billship_handler(request, form)
        tax_handler(request, form)
    except checkout.CheckoutError as e:
        checkout_errors.append(e)
        
    #create the session order and run setup for totals
    order = form.save(commit=False)
    order.setup_express(request)
    
    if total:
        if total != str(order.total):
            error(request, 'The cart total and order total do not match. Please start over.')
            return redirect('shop_express_checkout_cancel')
        
    if request.POST.get("back") is not None:
        # Back button in the form was pressed - load the order form
        # for the previous step and maintain the field values entered.
        step -= 1
        form = form_class(request, step, initial=initial)     
    #SetExpressCheckout here and redirect to PayPal
    elif request.cart.has_items():
        
        _shipping = request.cart.need_to_ship()
        
        if step == 1:
            
            _bill_data = {'paymentrequest_0_amt':order.total,
                      'returnurl':'https://localhost/shop/express-checkout/',
                      'cancelurl':'https://localhost/shop/express-checkout-cancel/',
                      'noshipping':0 if _shipping else 1,'allownote':"1","reqbillingaddress":1}
            if token:
                _bill_data['token']=token
                
            try:
                nvp_obj = wpp.setExpressCheckout(_bill_data)
            except PayPalFailure as e:
                print e
                if token:
                    del _bill_data['token']
                try:
                    nvp_obj = wpp.setExpressCheckout(_bill_data)
                except PayPalFailure as e:
                    error(request, str(e)+' please try again or use our Checkout')
                    return redirect('shop_express_checkout_cancel')
                 
            # Update the step so that we don't rely on POST data to take us back to
            # the same point in the checkout process.
            
            request.session["express_order"]=initial   
            request.session["express_order"]["step"] = 3
            request.session["express_order"]["total"] = str(order.total)
            request.session['express_order']['token']=nvp_obj.token
            
            request.session.modified = True
            _commit=True if not _shipping else False
            return HttpResponseRedirect(express_endpoint_for_token(nvp_obj.token,commit=_commit))
        
        if step==3:
            payerid=request.session["express_order"].get('payerid',False)
            _session_order=request.session["express_order"]
            if getdetails:
                
                try:
                    nvp_obj = wpp.getExpressCheckoutDetails({'token':token})
                    print 'getdetails'
                    d=nvp_obj.response_dict
                    _session_order['billing_detail_business']=d.get('business','n/a')
                    _session_order['billing_detail_first_name']=d.get('firstname','n/a')
                    _session_order['billing_detail_last_name']=d.get('lastname','n/a')
                    _session_order['billing_detail_country']=d.get('countrycode','n/a')
                    _session_order['billing_detail_state']=d.get('state','n/a')
                    _session_order['billing_detail_street']=d.get('street','n/a')+' '+d.get('street2','')
                    _session_order['billing_detail_state']=d.get('state','n/a')
                    _session_order['billing_detail_postcode']=d.get('zip','n/a')
                    _session_order['billing_detail_city']=d.get('city','n/a')
                    _session_order['billing_detail_email']=d.get('email','n/a')
                    _session_order['shipping_detail_business']=d.get('business','n/a')
                    _session_order['shipping_detail_first_name']=d.get('firstname','n/a')
                    _session_order['shipping_detail_last_name']=d.get('lastname','n/a')
                    _session_order['shipping_detail_country']=d.get('shiptocountrycode','n/a')
                    _session_order['shipping_detail_state']=d.get('shiptostate','n/a')
                    _session_order['shipping_detail_street']=d.get('shiptostreet','n/a')+' '+d.get('shiptostreet2','')
                    _session_order['shipping_detail_state']=d.get('shiptostate','n/a')
                    _session_order['shipping_detail_postcode']=d.get('shiptozip','n/a')
                    _session_order['shipping_detail_city']=d.get('shiptocity','n/a')
                    _session_order['additional_instructions']=d.get('paymentrequest_0_notetext','')
                    _session_order['payerid']=nvp_obj.payerid
                    _session_order['getdetails']=False
                    initial = checkout.initial_order_data(request, form_class,express=True)
                    form = form_class(request, step, data=initial)
                    order = form.save(commit=False)
                    payerid=nvp_obj.payerid
                    request.session.modified = True
                except PayPalFailure as e:
                    checkout_errors.append(e)
            
            if token and payerid and not checkout_errors:
                
                if request.method == 'POST' or not _shipping:
                    print 'step 3 and post or _noshipping'
                    
                    try:
                        order.setup(request)
                        nvp_obj = wpp.doExpressCheckoutPayment({'token':token,'payerid':payerid,\
                                'paymentrequest_0_amt':order.total})
                          
                    except PayPalFailure as e:
                        
                        order.delete()
                        checkout_errors.append(e)
                        error(request,e)
                        return redirect('shop_express_checkout_cancel')
                    else:
                        order.complete(request,express=True)
                        order_handler(request, form, order)
                        return redirect("shop_complete")
                  
            else:
                error(request,checkout_errors)
                return redirect('shop_express_checkout_cancel')
         
     
    
    step_vars = checkout.EXPRESS_CHECKOUT_STEPS[step - 1]
    template = "shop/%s.html" % step_vars["template"]
    context = {"CHECKOUT_STEP_FIRST": step == checkout.EXPRESS_CHECKOUT_STEP_FIRST,
               "CHECKOUT_STEP_LAST": step == checkout.EXPRESS_CHECKOUT_STEP_LAST,
               
               "step_title": step_vars["title"], "step_url": step_vars["url"],
               "steps": checkout.EXPRESS_CHECKOUT_STEPS, "step": step, "form": form,
               'express':True,"shipping":_shipping}
    context.update(extra_context or {})
    
    return TemplateResponse(request, template,context)


@never_cache
def complete(request, template="shop/complete.html", extra_context=None):
    """
    Redirected to once an order is complete - pass the order object
    for tracking items via Google Anayltics, and displaying in
    the template if required.
    """
    
    try:
        order = Order.objects.from_request(request)
    except Order.DoesNotExist:
        raise Http404
    items = order.items.all()
    # Assign product names to each of the items since they're not
    # stored.
    skus = [item.sku for item in items]
    variations = ProductVariation.objects.filter(sku__in=skus)
    names = {}
    for variation in variations.select_related("product"):
        names[variation.sku] = variation.product.title
    for i, item in enumerate(items):
        setattr(items[i], "name", names[item.sku])
    #change user_id on order from Anon
    if request.session['user'] != request.user.id:
        order.user_id=request.session['user']
        order.save()
        user=User.objects.get(pk=order.user_id)
        token = default_token_generator.make_token(user)
        user = authenticate(uidb36=int_to_base36(user.id),
                            token=token,
                            is_active=True)
        auth_login(request, user)
    context = {"order": order, "items": items, "has_pdf": HAS_PDF,
               "steps": checkout.CHECKOUT_STEPS}
    context.update(extra_context or {})
    
        
    return TemplateResponse(request, template, context)


def invoice(request, order_id, template="shop/order_invoice.html",
            template_pdf="shop/order_invoice_pdf.html", extra_context=None):
    """
    Display a plain text invoice for the given order. The order must
    belong to the user which is checked via session or ID if
    authenticated, or if the current user is staff.
    """
    try:
        order = Order.objects.get_for_user(order_id, request)
    except Order.DoesNotExist:
        raise Http404
    
    context = {"order": order}
    context.update(order.details_as_dict())
    context.update(extra_context or {})
    context = RequestContext(request, context)
    if HAS_PDF and request.GET.get("format") == "pdf":
        response = HttpResponse(content_type="application/pdf")
        name = slugify("%s-invoice-%s" % (settings.SITE_TITLE, order.id))
        response["Content-Disposition"] = "attachment; filename=%s.pdf" % name
        html = get_template(template_pdf).render(context)
        pisa.CreatePDF(html, response)
        return response
    return TemplateResponse(request, template, context)


@login_required
def order_history(request, template="shop/order_history.html",
                  extra_context=None):
    """
    Display a list of the currently logged-in user's past orders.
    """
    all_orders = (Order.objects
                  .filter(user_id=request.user.id)
                  .annotate(quantity_total=Sum('items__quantity')))
    orders = paginate(all_orders.order_by('-time'),
                      request.GET.get("page", 1),
                      settings.SHOP_PER_PAGE_CATEGORY,
                      settings.MAX_PAGING_LINKS)
    context = {"orders": orders, "has_pdf": HAS_PDF}
    context.update(extra_context or {})
    return TemplateResponse(request, template, context)


@login_required
def invoice_resend_email(request, order_id):
    """
    Re-sends the order complete email for the given order and redirects
    to the previous page.
    """
    try:
        order = Order.objects.get_for_user(order_id, request)
    except Order.DoesNotExist:
        raise Http404
    if request.method == "POST":
        checkout.send_order_email(request, order)
        msg = _("The order email for order ID %s has been re-sent" % order_id)
        info(request, msg)
    # Determine the URL to return the user to.
    redirect_to = next_url(request)
    if redirect_to is None:
        if request.user.is_staff:
            redirect_to = reverse("admin:shop_order_change", args=[order_id])
        else:
            redirect_to = reverse("shop_order_history")
    return redirect(redirect_to)
