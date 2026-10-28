# Coupon Site Status

Last checked: 2026-10-25, using ZenRows with Cozy Earth (`cozyearth.com`) as the test store. 62 sites so far (last updated 2026-10-28).

| Site | Status | Problem |
|---|---|---|
| hotdeals | ✅ Working | None. Store pages load and the codes are in the page HTML. |
| coupert | ✅ Working | `www.coupert.com/shop/{domain}` loads with a plain request; the old `usa.coupert.com/coupons/...` URLs block most requests. Full codes are in the page data. |
| rakuten | ❌ Not possible | Store pages show only cash back rates, no coupon codes. Tested on Kohl's, a store with many coupons, and the page still had no codes. Rakuten also only opens from a US IP. |
| karmanow | ❌ Not possible | Karma has no public store or coupon pages. Coupons only show inside the Karma browser extension after logging in. |
| discounttime | ❌ Not possible | The site is broken: its HTTPS certificate is invalid and over HTTP it returns an almost empty page. (Earlier it showed a "Checking your browser" screen and its `robots.txt` blocks all crawlers.) |
| capitalone | ✅ Working | None. Codes are shown on the store page. |
| retailmenot | ✅ Working | None for finding pages with codes. The code text itself is only revealed after clicking "Show Code", so pulling the actual codes needs an extra step. |
| honey | ✅ Working | None. Codes are embedded in the page data. |
| rebates | ✅ Working | rebates.com is a different site from RebatesMe (added 2026-10-28). Store pages always load, so a page counts only if it holds a coupon code for the store. Codes are in the page data. |
| rebatesme | ✅ Working | None. Codes are embedded in the page data. |
| slickdeals | ❌ Not possible | Slickdeals removed its store coupon pages. Every `/coupons/` URL returns 410 Gone, even for big stores. |
| coupons | ✅ Working | None. Codes are embedded in the page data. |
| dontpayfull | ✅ Working | Active and expired codes sit in separate sections. Active codes are masked (e.g. `***UMMER`) until clicked; only expired ones are shown in full. Only active code cards count. |
| offers | ✅ Working | None for finding pages with codes. Like RetailMeNot, the code text is only revealed after clicking "Reveal Code". |
| savings | ⚠️ Partly working | Works, but many stores show no codes of their own (Nike and Kohl's had none); the codes on those pages belong to other sponsored brands. |
| promocodes | ✅ Working | None. Codes are embedded in the page data. |
| couponchief | ✅ Working | None for finding pages with codes. Only the last character of each code is shown until it is clicked. Expired cards don't count. |
| goodshop | ✅ Working | None for finding pages with codes. Most codes are masked (e.g. `••••••AN`) in the page data. |
| simplycodes | ✅ Working | None. Codes are in the page HTML. |
| tenereteam | ✅ Working | None. Codes are embedded in the page data. |
| couponcause | ✅ Working | None for finding pages with codes. The code text is only revealed after clicking "Show Code". |
| goodsearch | ✅ Working | None for finding pages with codes. Same company and layout as Goodshop; most codes are masked. |
| valuecom | ✅ Working | Sometimes blocks requests (422) or returns a cut-off page with no content, but retries usually get through. Codes are embedded in the page data. ("valuecom" is valuecom.com; value.com is an unrelated parked domain.) |
| lovecoupons | ✅ Working | None. Each offer is marked as coupon or sale in the page HTML. |
| refermate | ✅ Working | None. Codes are embedded in the page data. |
| discountreactor | ✅ Working | None for finding pages with codes. Codes are masked (e.g. `••••••OZY`) until clicked. |
| couponbox | ⚠️ Partly working | Works, but many stores show only deals in their own list (Cozy Earth, Sephora, Cotopaxi had no codes); the codes on those pages belong to other cross-sell stores. Checked on Look Fantastic: codes found. |
| dealdrop | ✅ Working | None for finding pages with codes. Only the last 2 characters of each code are shown until clicked. |
| dazzdeals | ✅ Working | None for finding pages with codes. Only the last 4 characters of each code are shown until clicked. Unknown stores get blocked (422) instead of a 404. |
| greenpromocode | ✅ Working | None for finding pages with codes. Only the last 4 characters of each code are shown until clicked. ("greenpromocodes" is greenpromocode.com; greenpromocodes.com does not exist.) |
| couponbind | ✅ Working | None. Full codes are in the page HTML. |
| deala | ✅ Working | None for finding pages with codes. The code text is only revealed after clicking "Get code". |
| promopro | ✅ Working | None for finding pages with codes. Only the last 4 characters of each code are shown until clicked. |
| troupon | ✅ Working | None. Codes are in the page HTML. |
| worthepenny | ✅ Working | None. Codes are in the page HTML. |
| savingheist | ✅ Working | Some pages first show a "One moment, please..." challenge, which a JS-rendered retry gets past. Full codes are in the page HTML. |
| coupongrouphy | ✅ Working | Small WordPress site; pages usually show a challenge first, so JS rendering is needed. Store URLs often end in "thuy", which makes them hard to guess. |
| joincheckmate | ✅ Working | None for finding pages with codes. Codes are masked (e.g. `INS####`). |
| gocashback | ✅ Working | Coupons only appear with JS rendering, so every check costs a rendered request. |
| trustdeals | ✅ Working | Pages also list generic "most common codewords at similar shops" (SAVE10, WELCOME). These count as codes, to match the live project. |
| couponupto | ✅ Working | None. Full codes are in the page HTML. |
| promosreview | ✅ Working | Store URLs use the name in its original case (`CozyEarth`), so lowercase or hyphenated names don't work. |
| faircoupons | ❌ Not possible | The site is down: the server does not respond (timeouts directly, 422 through ZenRows, Cloudflare 522 earlier). No other domain found. |
| getscoupon | ✅ Working | None. Codes are in the page HTML (same layout as PromosReview). |
| rediscount | ✅ Working | rediscount.com is a parked domain; the real site is reediscount.com (double e), which is what the function uses. |
| shipthedeal | ✅ Working | None. The page header states how many coupon codes the store has. |
| couponblender | ✅ Working | Unknown stores show the homepage with status 200, so the page's canonical URL is checked. |
| xyzcodes | ✅ Working | Pages often show a "One moment, please..." challenge first, which the JS-rendered retry gets past. Store URLs vary (`yardzen`, `golfballs-com`). |
| coponcodes | ✅ Working | Store URLs vary a lot (`aosom-uk`, `bioptimizers-com`, `avidlove-promo-codes`), so some stores may be missed. Full codes are in the page HTML. |
| dealam | ✅ Working | Some "code" cards are ads for other offers; those are skipped. |
| latestdeals | ✅ Working | UK site behind an AWS WAF challenge, so every check needs JS rendering. Shows only the last 2 characters of each code. |
| kotag | ✅ Working | The real site is kotag.co (kotag.com is a domain for sale). Influencer promo codes, full codes in the page data. |
| zensaving | ❌ Not possible | The real site is zensaving.com (zensavings.com is parked). It is live, but its store pages list only deals: 16 stores checked, 130+ offers, no codes. |
| couponsgo | ✅ Working | The real site is couponsgo.org (.com and .net are parked). Many stores have no page of their own but appear in the site search. Shows only the last 4 characters of each code. |
| couponterra | ✅ Working | Mostly prop trading firms and forex brokers, but some retail stores too (Cabela's). Full codes are in the page HTML. Unknown stores return 422, not 404. |
| couponkiss | ✅ Working | The real site is couponskiss.com (couponkiss.com is a domain for sale). Full codes are in the page HTML. |
| couponpromo | ✅ Working | The real site is couponpromo.net (.com is for sale). Store page URLs are irregular, so the site search is used. Full codes are in the page HTML. |
| knoji | ❌ Not possible | Knoji no longer has its own pages. Every store URL returns 410 Gone and the homepage redirects to SimplyCodes, which is covered separately. |
| swagbucks | ❌ Not possible | Store pages show only cash back rates, no coupon codes (checked on Kohl's). Pages also need JavaScript rendering to show anything. |
| joinsmarty | ❌ Not possible | No public store pages could be found. Store URLs show the generic homepage, and category pages list no stores without signing up. |
| dealspotr | ❌ Not possible | Dealspotr no longer has its own pages. Every store URL redirects to SimplyCodes, which is covered separately. |
| dealcatcher | ❌ Not possible | Store pages load, but codes are not shown even for big stores like Nike. Offers only have a "Get Promo" button. |

## Details

### hotdeals
- URL format: `https://www.hotdeals.com/coupons/{store}-promo-codes`
- Each coupon card lists the store domain and the code, so codes for other brands on the same page are not counted.
- Cozy Earth result: page found with 24 codes.

### coupert
- Working URL format: `https://www.coupert.com/shop/{storeDomain}` (e.g. `/shop/b-spokes.co`). Loads with a plain request in about 3 seconds.
- The old format `https://usa.coupert.com/coupons/{store}-promo-code` returns 422 on most attempts, even with premium proxy, US proxy, JS rendering and antibot.
- Unknown stores show the Coupert homepage with status 200.
- The store's own codes are in the Next.js data at `props.pageProps.storeInfo.coupons` (`"code":"NEWBIKE200OFF"`). Other codes on the page (`WAG10`, `TRYKOSAS`) belong to featured stores and are skipped.
- Results: b-spokes 1 code (`NEWBIKE200OFF`). Cozy Earth and NOBULL have store pages but 0 coupons.

### rakuten
- URL format: `https://www.rakuten.com/shop/{store}`
- For a store Rakuten doesn't carry, the page still returns status 200 and shows "The page you're looking for does not exist".
- Without a US IP, the page shows "Rakuten isn't available in your region".
- Cozy Earth result: not on Rakuten.

### karmanow
- The store URLs we tried (`/stores/...`, `/store/...`, `/coupons/...`) all show an error page.
- The site map lists no store pages, only product "cashback picks".

### discounttime
- Every URL, including the homepage and sitemap, returns a browser verification page.
- The site's `robots.txt` blocks all crawlers except search engines (`User-agent: * Disallow: /`).
- Rechecked on 2026-10-26: `https://www.discounttime.com/` fails with an invalid SSL certificate, and `http://www.discounttime.com/` returns a 1.4 KB page with no content. The site is not working.

### capitalone
- URL format: `https://capitaloneshopping.com/s/{storeDomain}/coupon` (uses the domain, e.g. `cozyearth.com`)
- Each code offer has a "Copy code SHOP10 and open ..." button. Deals say "No code required".
- Cozy Earth result: page found, but both offers are deals with no code. Checked on Kohl's: codes found.

### retailmenot
- URL format: `https://www.retailmenot.com/view/{storeDomain}`
- Stores RetailMeNot doesn't carry redirect to the homepage with status 200.
- Offers marked COUPON have a "Show Code" button; expired offers are ignored.
- Cozy Earth result: not on RetailMeNot. Checked on Nike: codes found.

### honey
- URL format: `https://www.joinhoney.com/shop/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 3 public codes (`UNLOCKCOZY`, `CE-CADE`, `CE-DISCOVERELLE`).

### rebates (rebates.com)
- URL format: `https://rebates.com/coupons/{storeDomain}`
- Every store gets a page, even with no offers, so the page counts only if its Next.js data has an offer with `"type":"coupon"` and a code whose `"store"` is the store's domain (other stores' offers are mixed in).
- Results (2026-10-28): Tommy John 4 codes, Macy's 216 codes; Cozy Earth and NOBULL pages exist but have no codes, so no link.
- The link check uses `rebates_codes()` from main.py, the same parsing as the code scraper.

### rebatesme
- URL format: `https://www.rebatesme.com/en/stores/{store}` (e.g. `cozyearth`)
- Unknown stores return a generic stores page with status 200, so the store name in the page data is checked.
- Cozy Earth result: store listed, but no coupon codes right now. Checked on Macy's: code found.

### slickdeals
- Tried `https://slickdeals.net/coupons/{store}/` for real and fake stores; all return 410 Gone.

### coupons
- URL format: `https://www.coupons.com/coupon-codes/{store}` (e.g. `cozyearth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 3 codes (`SA50-8wtC54`, `WELCOME10`, `CE-CADE`).

### dontpayfull
- URL format: `https://www.dontpayfull.com/at/{storeDomain}`
- Unknown stores return 404.
- Cozy Earth result: page found with 15 codes, 2 active (`GETCOZY50`, `MOMFIRST`) and the rest expired.
- Correction (2026-10-27): active codes are masked (`***LCOZY`), and only expired codes show in full. Cozy Earth has 12 active masked codes (ending Nov 20 to Nov 23, 2026), not "all expired" as reported before.

### offers
- URL format: `https://www.offers.com/stores/{store}/`
- Unknown stores redirect to the homepage, which has its own code offers, so the page's canonical URL is checked.
- Cozy Earth result: not on Offers.com. Checked on Nike: codes found.
- Some stores carry a country suffix, e.g. `https://www.offers.com/stores/macys-us/` (`/stores/macys/` returns 404), so both forms are tried.

### savings
- URL format: `https://www.savings.com/coupons/{storeDomain}`
- Unknown stores return 404.
- Cozy Earth result: not on Savings.com. Checked on Teleflora: codes found. Nike and Kohl's pages had no codes of their own.

### dealcatcher
- URL format: `https://www.dealcatcher.com/coupons/{store}`
- Unknown stores return 404.
- Cozy Earth result: page found with 4 offers, none with a code. Nike: 12 offers, none with a code.

### promocodes
- URL format: `https://www.promocodes.com/{store}-coupons` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 3 codes (`GETCOZY`, `COZYWELCOME`, `GETCOZY15`).

### couponchief
- URL format: `https://www.couponchief.com/{store}` (e.g. `cozyearth`)
- Unknown stores return 404.
- The sidebar shows full codes for other stores; only the store's own coupon cards are counted.
- Cozy Earth result: page found with 14 code cards (2 active, the rest expired).

### goodshop
- URL format: `https://www.goodshop.com/coupons/{storeDomain}`
- Unknown stores return 404.
- Cozy Earth result: page found with about 21 codes, most masked (`CE-DISCOVERELLE` shown in full).

### simplycodes
- URL format: `https://simplycodes.com/store/{storeDomain}`
- Unknown stores return 404.
- Related-store buttons on the page carry other stores' codes; only buttons linking to the store's own site are counted.
- Cozy Earth result: page found with codes such as `COZY15`, `COZYWELCOME`, `CE-DISCOVER`, `MOM15`.

### dealspotr
- `https://dealspotr.com/promo-codes/{storeDomain}` returns the SimplyCodes page (canonical `simplycodes.com/store/...`). Unknown stores return 410 Gone.

### tenereteam
- URL format: `https://{store}.tenereteam.com/coupons` (hyphenated subdomain, e.g. `cozy-earth`). The subdomain root is a reviews page.
- Unknown stores return 404.
- Cozy Earth result: page found with codes such as `GETCOZY50`, `WELCOME10`, `COZYWELCOME`, `GETCOZY`.

### couponcause
- URL format: `https://couponcause.com/stores/{store}/` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return status 200 with an "Oops Page Not Found!" page (canonical `/404/`), so the canonical URL is checked.
- Code offers link to a reveal URL with `?_c=`; deals don't.
- Cozy Earth result: page found with 6 code offers (e.g. `CE10` is mentioned in the offer text).

### goodsearch
- URL format: `https://www.goodsearch.com/coupons/{storeDomain}`
- Same company as Goodshop with the same page data. Unknown stores return 404.
- Cozy Earth result: page found with about 21 active codes, most masked (`CE-DISCOVERELLE` shown in full).

### valuecom
- URL format: `https://{store}.valuecom.com/` (no hyphens, e.g. `cozyearth`). Some stores use `https://store{store}.valuecom.com/`, so both are tried.
- Unknown stores return 404, but the site sometimes returns 422 (blocked) instead.
- Cozy Earth result: page found with 11 codes (e.g. `SAMANTHA55`, `SA50-tuoiVn`, `STILLCOZY`, `JOURNAL`).

### lovecoupons
- URL format: `https://www.lovecoupons.com/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Offer types: 2 = coupon with a code, 3 = sale, 9 = shipping.
- Cozy Earth result: page found, but its only offer is a sale (no code). Checked on Kohl's: codes found.

### knoji
- `knoji.com/promo-codes/{store}/`, `{store}.knoji.com/promo-codes/` and `knoji.com/brand/{store}/` all return 410 Gone. The homepage redirects to `simplycodes.com`.

### swagbucks
- URL format: `https://www.swagbucks.com/shop/{store}-coupons` (needs JS rendering)
- Unknown stores show "We couldn't find the store you were looking for".
- Kohl's page: cash back rate and store description only, no codes.
- Cozy Earth result: not on Swagbucks.

### joinsmarty
- `joinsmarty.com/{store}-coupons` returns the generic homepage, `joinsmarty.com/stores/{store}` returns 404.
- Category pages (e.g. `/stores/HealthBeauty`), even with JS rendering, show navigation and a sign-up form but no store links.

### refermate
- URL format: `https://refermate.com/store/{store}/promo-codes` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Deals are listed with "No Code Needed" instead of a code.
- Cozy Earth result: page found with 7 codes (`SA50-8wtC54`, `CE-RESTED`, `GETCOZY15`, `SPRINGCLEAN`, `NEWCOMFORT`, `FALL30`, `GETCOZY`).

### discountreactor
- URL format: `https://www.discountreactor.com/coupons/{storeDomain}`; some stores use a plain name instead (e.g. `/coupons/lindseyleighjewelry`), so both are tried.
- Unknown stores return 404.
- Cozy Earth result: page found with 35 masked code offers.

### couponbox
- URL format: `https://www.couponbox.com/coupons/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- The store's own offers come before the expired list in the page data; "copy_paste" offers there are codes, "instructions" offers are sign-up gifts.
- Cozy Earth result: page found, but only deals and a newsletter gift card, no codes.

### rebatesme (asked for again)
- Already covered by `rebatesme()`; no new function was added.

### dealdrop
- URL format: `https://www.dealdrop.com/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 4 codes shown (title says 6 working codes).

### dazzdeals
- URL format: `https://www.dazzdeals.com/store/{store}/` (hyphenated, e.g. `cozy-earth`)
- Unknown stores were blocked (422) rather than returning 404, so a missing store just comes back empty.
- Cozy Earth result: page found with 1 code (ends in `PNIK`).

### greenpromocode
- URL format: `https://www.greenpromocode.com/coupons/{store}/` (hyphenated, e.g. `cozy-earth`)
- `greenpromocodes.com` (with an s) does not resolve.
- Unknown stores return 404.
- Cozy Earth result: page found with 26 codes (shown as their last 4 characters, e.g. `OZY5`, `COZY`, `REST`).

### couponbind
- URL format: `https://www.couponbind.com/coupons/{storeDomain}`
- Unknown stores return 404.
- Cozy Earth result: page found with 10 codes (e.g. `CE-DISCOVERELLE`, `SLEEPBLANKET`, `MOM15`, `COZYWELCOME`, `GETCOZY15`, `COZYWINTER15`).

### deala
- URL format: `https://deala.com/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 19 code offers.

### promopro
- URL format: `https://www.promopro.com/coupon-codes/{store}` (no trailing slash)
- Unknown stores return 410 Gone.
- Cozy Earth result: not on PromoPro. Checked on Meshy: codes found.

### troupon
- URL format: `https://{store}.troupon.com/` (hyphenated subdomain, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 19 codes (e.g. `HOMELIFE`, `WELCOME10`, `COZY15`, `GETCOZY`, `HELLOSUMMER`).

### worthepenny
- URL format: `https://{store}.worthepenny.com/coupon/` (no hyphens, e.g. `cozyearth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 12 codes (e.g. `CE-CADE`, `UNLOCKCOZY`, `SA50-8wtC54`, `WELCOME10`, `COZYMOMENTS`).

### savingheist
- URL format: `https://savingheist.com/store/{store}/`; some stores use `/store/{store}-coupons/`, so both are tried.
- Unknown stores return 404.
- Cozy Earth result: not on SavingHeist. Checked on ALLPOWERS: codes found.

### coupongrouphy
- URL format: `https://coupongrouphy.com/dealstore/{store}/`; many stores use a "thuy" suffix (`/dealstore/dreamlandbabycothuy/`), so both are tried.
- Without JS rendering the site shows a "One moment, please..." challenge page.
- Cozy Earth result: not on CouponGrouphy. Checked on Dreamland Baby: codes found.

### joincheckmate
- URL format: `https://joincheckmate.com/merchants/{storeDomain}`
- Unknown stores return 404.
- Cozy Earth result: page found with 25 masked codes.

### gocashback
- URL format: `https://www.gocashback.com/stores/{store}` (no hyphens, e.g. `cozyearth`)
- Unknown stores return 404. Without JS rendering the page only says how many coupons exist, not the codes.
- Cozy Earth result: page found with 3 codes (`COZY5`, `LIFEMART`).

### trustdeals
- URL format: `https://www.trustdeals.com/view/{storeDomain}`
- Unknown stores return 404.
- Sections: "active" (real offers), "common" (generic guessed codewords) and "expired".
- Cozy Earth result: page found, but only generic guessed codewords, no real codes. Checked on The Body Shop: codes found.
- Changed 2026-10-27: the generic "common codeword" offers now count as codes, so Cozy Earth gets a link (SAVE10, WELCOME, SAVE20, TEST, WELCOME15), matching the live project.

### couponupto
- URL format: `https://www.couponupto.com/coupons/{store}` (hyphenated, e.g. `cozy-earth`)
- Unknown stores redirect to the homepage (canonical `couponupto.com`), so the canonical URL is checked.
- Cozy Earth result: page found with 3 codes (`SPECIALDEAL2023`, `USA12AFF`, `NineZ9KHNB47`).

### promosreview
- URL format: `https://www.promosreview.com/store/{Name}-coupon-codes` (e.g. `CozyEarth`)
- Unknown stores return a small generic page with status 200.
- Cozy Earth result: page found with 30 codes (e.g. `PJ-BOGO`, `FALL30`, `COZY15`, `OPRAH20`, `SLEEPDUVET`).

### faircoupons
- `https://www.faircoupons.com/stores/{store}` returns 422 on every attempt through ZenRows; a direct request times out after 20 seconds. The site is down.
- faircoupons.net and faircoupons.org don't exist.

### getscoupon
- URL format: `https://www.getscoupon.com/store/{storeDomain}` (case doesn't matter; canonical uses `CozyEarth.com`)
- Unknown stores return a small generic page with status 200.
- Cozy Earth result: page found with 30 codes (e.g. `PJ-BOGO`, `COMFORT35`, `FALL30`, `DUVET30`).

### rediscount (reediscount.com)
- `rediscount.com` redirects to a parked-domain lander. The coupon site is `reediscount.com`.
- URL format: `https://www.reediscount.com/{store}/` (hyphenated, e.g. `bambu-lab`); some stores use the domain (`/goskoot.com/`), so both are tried.
- Unknown stores return 404.
- Cozy Earth result: not on ReeDiscount. Checked on Bambu Lab: codes found.

### shipthedeal
- URL format: `https://shipthedeal.com/store/{store}-coupon` (hyphenated, e.g. `cozy-earth`)
- Unknown stores return 404.
- Cozy Earth result: page found with 2 offers but "0 Coupon Code(s)", so no codes. Checked on Uber Eats: 3 codes found.

### couponblender
- URL format: `https://www.couponblender.com/{store}/`
- Unknown stores show the homepage (canonical `https://www.couponblender.com//`).
- Cozy Earth result: not on Coupon Blender. Checked on Shopbop: codes found.

### couponsgo (couponsgo.org)
- Earlier marked "not possible" because couponsgo.com and couponsgo.net are parked; the coupon site is couponsgo.org.
- URL formats: `https://www.couponsgo.org/{store}` (e.g. `shopbop`, `walmart.com`) and `https://www.couponsgo.org/search?query={name}`.
- Each offer carries the store domain (`data-domain="nobullproject.com"`); code offers show the last 4 characters of the code.
- Same company and code data as couponpromo.net: NOBULL's code tails here (`VX20`, `KE10`) match couponpromo.net's full codes (`GOVX20`, `TAKE10`).
- Results: Cozy Earth 3 codes (search page), NOBULL 13 codes (search page), Shopbop 36 codes (store page).

### couponpromo (couponpromo.net)
- Earlier marked "not possible" because couponpromo.com is for sale; the coupon site is couponpromo.net.
- Store pages have irregular URLs (`/target/target-promo-code.htm`, `/jcpenney/jcpenney-coupon.htm`), so `https://couponpromo.net/search?query={name}` is used.
- Each result has the full code and the store domain: `data-domain="nobullproject.com"` ... `data-clipboard-text="GOVX20"`.
- Results: NOBULL 14 codes (e.g. `GOVX20`, `TAKE10`, `BIGCAT35`, `AFTERDARK`), Cozy Earth and Shopbop also found.

### couponterra
- Earlier marked "not possible" because the homepage only shows prop trading firm coupons. It also has some retail stores, e.g. `https://www.couponterra.com/coupons/cabelas.com`.
- URL format: `https://www.couponterra.com/coupons/{storeDomain}`
- Unknown stores always return 422 ("Could not get content"), so the function makes a single request without retries.
- Full codes are in the page: `data-coupon-id="1235" data-code="VORTEX15"`.
- Results: Cabela's 2 codes (`VORTEX15`, `SPRINGFISHING`), Instant Funding 11 codes. Cozy Earth and NOBULL are not listed.

### couponkiss (couponskiss.com)
- `couponkiss.com` redirects to a domain-sale page (same server as kotag.com), which is why ZenRows refused it. The coupon site is `couponskiss.com`.
- URL format: `https://{store}.couponskiss.com/` (e.g. `gform.couponskiss.com`). Unknown stores show the homepage (canonical `www.couponskiss.com`).
- Codes are in `data-clipboard-text`; deals say "Click to Redeem".
- Results: G Form 10 codes (e.g. `WELCOMEBACK`, `GONEXTLEVEL`). Cozy Earth and NOBULL are not listed.

### xyzcodes
- URL format: `https://www.xyzcodes.com/store/{store}/` (e.g. `yardzen`); some stores use the domain with hyphens (`golfballs-com`), so both are tried.
- Unknown stores return 404.
- Cozy Earth result: not on XYZCodes. Checked on Yardzen: codes found.

### coponcodes
- "coponcodes" is coponcodes.com (couponcodes.com just redirects to RetailMeNot).
- URL format: `https://coponcodes.com/store/{store}/`, with irregular suffixes on many stores.
- Unknown stores return 404.
- Cozy Earth result: not found under any of the tried URLs. Checked on BiOptimizers: codes found.

### dealam
- URL format: `https://www.dealam.com/coupons/{store}/` (hyphenated, e.g. `cozy-earth`). There is also a deals page at `/stores/{store}.html`.
- Unknown stores return 404.
- Cozy Earth result: page found with 36 code offers (e.g. `COZY15`, `COZYWELCOME`, `GETCOZY`, `COZY5`).

### latestdeals
- URL format: `https://www.latestdeals.co.uk/retailers/{storeDomain}` (`/vouchers/{store}` points to the same page).
- Every page sits behind an AWS WAF challenge; without JS rendering only the challenge script comes back.
- Unknown stores return 404.
- Cozy Earth result: page found with 1 active code (ends in `24`) and 5 expired UK codes. Checked on Amazon UK: codes found.

### kotag (kotag.co)
- `kotag.com` redirects to hugedomains.com (domain for sale). The coupon site is `kotag.co`, an influencer promo code site.
- URL format: `https://kotag.co/us/marques/codes-promo-{store}/`. Unknown stores show the brand list.
- Codes are in the Next.js data at `props.pageProps.coupons`, each with the influencer and an `expired` flag.
- Cozy Earth result: 6 influencer codes (`CHAMPAGNE45` Shannyn Champagne, `AWKWARDTORTOISEE40` Julianne, `BLESSINGANDSASS45` Blessing & Sass, `AMELIAADINEEN40` Amelia Dineen, `JAY40` Jeremy Cash, `ALLIE` Allie Beth Stuckey).

### zensaving (zensaving.com)
- `zensavings.com` (with s) redirects to a parked-domain lander. The live site is `zensaving.com`.
- URL format: `https://zensaving.com/store/{store}` (e.g. `/store/1ink`, `/store/alitura-naturals`).
- 16 stores checked (AliExpress, Banggood, KidKraft, 1ink and others): every offer is a "Get Deal" link, none has a code.
