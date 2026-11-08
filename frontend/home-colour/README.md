# Colourful CV Studio Home

React source for the original Studio Home. `static/app.js` passes current saved
evidence through `buildColourHomeModel` in `static/home-beta.js`; the component
does not fetch records or perform application or automation writes.

Install the pinned dependencies with `npm ci`, then run `npm run typecheck` and
`npm run build`. The build writes `../../static/home-colour.js` and
`../../static/home-colour.css`. Both are served locally under Studio's existing
CSP. No runtime CDN is required.

The Home preserves Paris-time date filters, explicit historical estimates,
unknown values, live run controls in the Studio shell, and saved local card
order. The Paris clock updates separately from the evidence charts and pauses
while the page is hidden. Card reordering respects the reduced-motion setting.
The seven-day application chart always uses the last seven Paris calendar days,
independent of the date filter. Missing records remain unknown.

Browser regression checks use the isolated Studio fixture:
`test_home_browser.cjs`, `test_browser.cjs`, and `test_apply_browser.cjs`.
