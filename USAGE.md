# Coupon site URL finder (url.py)

Each function checks one coupon site and stores the store page URL in `handles.json`, but only if that page has codes for the store. If no valid page is found, the site's list stays empty (`[]`).

Saved URLs are re-checked on every run (one request per saved URL):
- Page still has codes: the URL is kept.
- Page is gone (404) or has no codes any more: the URL is removed and the site is searched again.
- Request blocked (422), failed, or the page came back cut off (no `<body>`, retried once): the URL is kept, since that says nothing about the page.

If a page comes back as a "One moment, please..." challenge page, it is fetched again with JS rendering.

## Store parameters

```python
storeDomain = "cozyearth.com"
storeQuery = {"handle": "cozy-earth", "query": ["Cozy Earth"]}
```

## Functions

```python
handles = coupert(handles, storeDomain, storeQuery)
handles = discounttime(handles, storeDomain, storeQuery)
handles = karmanow(handles, storeDomain, storeQuery)
handles = rakuten(handles, storeDomain, storeQuery)
handles = hotdeals(handles, storeDomain, storeQuery)
handles = slickdeals(handles, storeDomain, storeQuery)
handles = capitalone(handles, storeDomain, storeQuery)
handles = retailmenot(handles, storeDomain, storeQuery)
handles = honey(handles, storeDomain, storeQuery)
handles = rebates(handles, storeDomain, storeQuery)
handles = rebatesme(handles, storeDomain, storeQuery)
handles = coupons(handles, storeDomain, storeQuery)
handles = dealcatcher(handles, storeDomain, storeQuery)
handles = dontpayfull(handles, storeDomain, storeQuery)
handles = offers(handles, storeDomain, storeQuery)
handles = savings(handles, storeDomain, storeQuery)
handles = dealspotr(handles, storeDomain, storeQuery)
handles = promocodes(handles, storeDomain, storeQuery)
handles = couponchief(handles, storeDomain, storeQuery)
handles = goodshop(handles, storeDomain, storeQuery)
handles = simplycodes(handles, storeDomain, storeQuery)
handles = tenereteam(handles, storeDomain, storeQuery)
handles = couponcause(handles, storeDomain, storeQuery)
handles = goodsearch(handles, storeDomain, storeQuery)
handles = valuecom(handles, storeDomain, storeQuery)
handles = lovecoupons(handles, storeDomain, storeQuery)
handles = knoji(handles, storeDomain, storeQuery)
handles = swagbucks(handles, storeDomain, storeQuery)
handles = joinsmarty(handles, storeDomain, storeQuery)
handles = refermate(handles, storeDomain, storeQuery)
handles = discountreactor(handles, storeDomain, storeQuery)
handles = couponbox(handles, storeDomain, storeQuery)
handles = dealdrop(handles, storeDomain, storeQuery)
handles = dazzdeals(handles, storeDomain, storeQuery)
handles = greenpromocode(handles, storeDomain, storeQuery)
handles = couponbind(handles, storeDomain, storeQuery)
handles = deala(handles, storeDomain, storeQuery)
handles = promopro(handles, storeDomain, storeQuery)
handles = troupon(handles, storeDomain, storeQuery)
handles = worthepenny(handles, storeDomain, storeQuery)
handles = savingheist(handles, storeDomain, storeQuery)
handles = coupongrouphy(handles, storeDomain, storeQuery)
handles = joincheckmate(handles, storeDomain, storeQuery)
handles = faircoupons(handles, storeDomain, storeQuery)
handles = gocashback(handles, storeDomain, storeQuery)
handles = trustdeals(handles, storeDomain, storeQuery)
handles = couponupto(handles, storeDomain, storeQuery)
handles = promosreview(handles, storeDomain, storeQuery)
handles = couponsgo(handles, storeDomain, storeQuery)
handles = getscoupon(handles, storeDomain, storeQuery)
handles = rediscount(handles, storeDomain, storeQuery)
handles = couponterra(handles, storeDomain, storeQuery)
handles = couponkiss(handles, storeDomain, storeQuery)
handles = shipthedeal(handles, storeDomain, storeQuery)
handles = couponpromo(handles, storeDomain, storeQuery)
handles = couponblender(handles, storeDomain, storeQuery)
handles = xyzcodes(handles, storeDomain, storeQuery)
handles = kotag(handles, storeDomain, storeQuery)
handles = zensaving(handles, storeDomain, storeQuery)
handles = coponcodes(handles, storeDomain, storeQuery)
handles = dealam(handles, storeDomain, storeQuery)
handles = latestdeals(handles, storeDomain, storeQuery)

printSummary(handles, storeDomain)  # sites with links first, then empty ones, then totals and time taken
```

## Site notes

| Site | Status | Notes |
|---|---|---|
| hotdeals | Works | `https://www.hotdeals.com/coupons/{slug}-promo-codes`. Counts the page only if a coupon card for `storeDomain` has a non-empty `data-code`. |
| coupert | Works | `https://www.coupert.com/shop/{storeDomain}` (loads without blocking, unlike `usa.coupert.com`). Counts the page only if `storeInfo.coupons` in the page data has a code; codes elsewhere on the page belong to other stores. |
| rakuten | Always empty | Store pages show cash back only, no coupon codes. No request is sent. |
| karmanow | Always empty | No public store pages; coupons are only in the extension. No request is sent. |
| discounttime | Always empty | Site is broken (invalid HTTPS certificate, empty page over HTTP). No request is sent. |
| capitalone | Works | `https://capitaloneshopping.com/s/{storeDomain}/coupon`. Counts the page only if it has a "Copy code ..." button. |
| retailmenot | Works | `https://www.retailmenot.com/view/{storeDomain}`. Counts the page only if a non-expired offer of type COUPON exists for `storeDomain`. Unknown stores redirect to the homepage. |
| honey | Works | `https://www.joinhoney.com/shop/{slug}`. Counts the page only if `publicCouponCount` > 0. |
| rebates | Works | `https://rebates.com/coupons/{storeDomain}` (rebates.com, not RebatesMe). Counts the page only if it has a coupon code for `storeDomain`; empty store pages still load. |
| rebatesme | Works | `https://www.rebatesme.com/en/stores/{slug}`. Counts the page only if the store's coupon list has a code. Unknown stores return a generic page with status 200. |
| slickdeals | Always empty | Coupon pages were removed (410 Gone). No request is sent. |
| coupons | Works | `https://www.coupons.com/coupon-codes/{slug}`. Counts the page only if a voucher in the page data has a code. |
| dealcatcher | Usually empty | `https://www.dealcatcher.com/coupons/{slug}`. Counts the page only if an offer in the Nuxt data has a non-empty code; even big stores (Nike) show none. |
| dontpayfull | Works | `https://www.dontpayfull.com/at/{storeDomain}`. Counts the page only if `#active-coupons` has a code card (`article.code`). Active codes are masked until clicked; codes under `#expired-coupons` don't count. |
| offers | Works | `https://www.offers.com/stores/{slug}/`, then `/stores/{slug}-us/` (e.g. `macys-us`). Counts the page only if the canonical is a store page and an offer has `hasCode: true`. Unknown stores redirect to the homepage. |
| promocodes | Works | `https://www.promocodes.com/{slug}-coupons`. Counts the page only if an offer in the page data has a `couponCode`. |
| couponchief | Works | `https://www.couponchief.com/{slug}`. Counts only the store's own coupon cards (with `data-coupon-id`) that hide a code; sidebar codes from other stores are skipped. Expired cards don't count. |
| goodshop | Works | `https://www.goodshop.com/coupons/{storeDomain}`. Counts the page only if a JSON-LD offer for this store has a `discountCode` (most are masked like `••••••AN`). |
| simplycodes | Works | `https://simplycodes.com/store/{storeDomain}`. Counts only code buttons whose merchant URL is the store's own site; related-store buttons are skipped. |
| tenereteam | Works | `https://{slug}.tenereteam.com/coupons`. Counts the page only if an offer in the page data has a `coupon_code`. |
| couponcause | Works | `https://couponcause.com/stores/{slug}/`. Counts the page only if the canonical is a store page and an offer links to a code reveal (`?_c=`). Unknown stores return 200 with a "Page Not Found" page. |
| goodsearch | Works | `https://www.goodsearch.com/coupons/{storeDomain}`. Same layout as Goodshop: counts the page only if a JSON-LD offer for this store has a `discountCode`. |
| valuecom | Works | `https://{slug}.valuecom.com/`, then `https://store{slug}.valuecom.com/` (slug without hyphens). Counts the page only if a JSON-LD offer has a `couponCode`. Often blocks requests (422) or cuts the page off, so it retries up to 8 times. |
| lovecoupons | Works | `https://www.lovecoupons.com/{slug}`. Counts the page only if an offer for this brand has `data-type="2"` (coupon); sales are type 3. |
| knoji | Always empty | Every store URL returns 410 Gone; the homepage redirects to SimplyCodes. No request is sent; use `simplycodes()`. |
| swagbucks | Always empty | Store pages show cash back only, no coupon codes (checked on Kohl's). No request is sent. |
| joinsmarty | Always empty | No public store pages found; store lists only show after signing up. No request is sent. |
| refermate | Works | `https://refermate.com/store/{slug}/promo-codes`. Counts the page only if the page data has a `couponCode` other than "No Code Needed". |
| discountreactor | Works | `https://www.discountreactor.com/coupons/{storeDomain}`, then `/coupons/{slug}` (no hyphens). Counts only masked code offers whose `data-shop-alias` is this store. |
| couponbox | Works for some stores | `https://www.couponbox.com/coupons/{slug}`. Counts only `copy_paste` offers in the store's own active list; many stores (Cozy Earth, Sephora) only show deals there. |
| dealdrop | Works | `https://www.dealdrop.com/{slug}`. Counts the page only if a code article (`<article id="code-...">`) has a "Show code" button. |
| dazzdeals | Works | `https://www.dazzdeals.com/store/{slug}/`. Counts the page only if a code button (`couponBtn ... showCouponCode`) exists. Unknown stores return 422 instead of 404. |
| greenpromocode | Works | `https://www.greenpromocode.com/coupons/{slug}/`. Counts only "Show Code" buttons that link to this store's page. (greenpromocodes.com does not exist.) |
| couponbind | Works | `https://www.couponbind.com/coupons/{storeDomain}`. Counts the page only if an offer has a `hiddenCode` (the full code). |
| deala | Works | `https://deala.com/{slug}`. Counts the page only if one of the store's own coupon cards has a "Get code" button. |
| promopro | Works | `https://www.promopro.com/coupon-codes/{slug}` (no trailing slash; unknown stores return 410). Counts the page only if an offer card is marked `detail_filter_code`. |
| troupon | Works | `https://{slug}.troupon.com/`. Counts only `data-coupon-code` buttons whose `data-coupon-url` is the store's own site. |
| worthepenny | Works | `https://{slug}.worthepenny.com/coupon/` (no hyphens). Counts only code cards whose `data-name` is this store. |
| savingheist | Works | `https://savingheist.com/store/{slug}/`, then `/store/{slug}-coupons/`. Counts the page only if a button has `data-coupon-id` and `data-code`. |
| coupongrouphy | Works | `https://coupongrouphy.com/dealstore/{slug}/`, then `/dealstore/{slug}thuy/`. Counts the page only if a `masked_coupon` has a code. Usually needs the JS-rendering retry. |
| joincheckmate | Works | `https://joincheckmate.com/merchants/{storeDomain}`. Counts the page only if a masked code (e.g. `INS####`) is shown. |
| faircoupons | Always empty | Site is down; the server does not respond. No request is sent. |
| gocashback | Works | `https://www.gocashback.com/stores/{slug}` (no hyphens). Always fetched with JS rendering, since coupons only appear after scripts run. Counts the page only if a "Code:" block exists; fetched a second time if the first render has none. |
| trustdeals | Works | `https://www.trustdeals.com/view/{storeDomain}`. Counts the page if a code offer exists, including the generic "most common codewords at similar shops" (SAVE10, WELCOME), to match the live project; expired codes are skipped. |
| couponupto | Works | `https://www.couponupto.com/coupons/{slug}`. Counts the page only if the canonical is a store page and an offer shows a code. Unknown stores redirect to the homepage. |
| promosreview | Works | `https://www.promosreview.com/store/{Name}-coupon-codes` (name without spaces, original case, e.g. `CozyEarth`). Counts only codes whose `data-url` is the store's own site. |
| getscoupon | Works | `https://www.getscoupon.com/store/{storeDomain}`. Same layout as PromosReview: counts only codes whose `data-url` is the store's own site. |
| rediscount | Works | Uses `reediscount.com` (rediscount.com is parked): `/{slug}/`, then `/{storeDomain}/`. Counts the page only if a code button (`data-code` + `data-store_info`) exists. |
| shipthedeal | Works | `https://shipthedeal.com/store/{slug}-coupon`. Counts the page only if its "N Coupon Code(s)" count is above 0. |
| couponblender | Works | `https://www.couponblender.com/{slug}/`. Counts the page only if the canonical is a store page and an offer is labelled "COUPON CODE". Unknown stores show the homepage. |
| xyzcodes | Works | `https://www.xyzcodes.com/store/{slug}/`, then `/store/{domain-with-hyphens}/`. Counts the page only if an offer link has `data-type="code"`. Pages often need the JS-challenge retry. |
| coponcodes | Works | `https://coponcodes.com/store/...`, trying `{slug}`, `{domain-with-hyphens}`, `{slug}-promo-codes` and `{slug}-coupon-code`. Counts the page only if a hidden code (`<span id="code-..." hidden>`) exists. |
| dealam | Works | `https://www.dealam.com/coupons/{slug}/`. Counts only `dx_code_coupons` offers that aren't ads (`new-class-ads`). |
| latestdeals | Works | `https://www.latestdeals.co.uk/retailers/{storeDomain}`. Checked through `/api/posts?merchantSlugDomain={storeDomain}&type=voucher&type=offer`; counts the page only if a post has a code. |
| kotag | Works | Uses `kotag.co` (kotag.com is for sale): `https://kotag.co/us/marques/codes-promo-{slug}/`. Influencer codes. Counts the page only if `pageProps.coupons` in the page data has a non-expired code. |
| zensaving | Always empty | Uses `zensaving.com` (zensavings.com is parked). Store pages `/store/{slug}` list only deals; 16 stores checked, none had a code. No request is sent. |
| couponsgo | Works | Uses `couponsgo.org` (.com and .net are parked): `/{slug}`, then `/{storeDomain}`, then `/search?query={name}`. Counts only offers whose `data-domain` is this store and whose button shows a code tail instead of "Get Deal". |
| couponterra | Works | `https://www.couponterra.com/coupons/{storeDomain}`. Mostly prop trading firms, but some retail stores (Cabela's). Fetched once without retries, since unknown stores always return 422. Counts the page only if a button has `data-coupon-id` and `data-code`. |
| couponkiss | Works | Uses `couponskiss.com` (couponkiss.com is for sale): `https://{slug}.couponskiss.com/`. Counts the page only if the canonical is the store subdomain and a `data-clipboard-text` holds a code (not "Click to Redeem"). |
| couponpromo | Works | Uses `couponpromo.net` (.com is for sale) and its search page `/search?query={name}`, since store page URLs are irregular. Counts the page only if an offer for this store's `data-domain` has a full code (`data-clipboard-text`). |
| dealspotr | Always empty | Every store page redirects to SimplyCodes. No request is sent; use `simplycodes()`. |
| savings | Works for some stores | `https://www.savings.com/coupons/{storeDomain}`. Counts only code offers whose provider is this store; many stores (Nike, Kohl's) only show codes for other sponsored brands. |
