# Company Logos Asset Library (CV Studio Offline Assets)

This repository contains authentic, source-backed company logos for offline rendering in CV Studio. Assets have transparent backgrounds; intrinsic brand shapes such as a full-bleed logo square remain intact. Render-time use makes no external network calls.

## Scope & Policy Compliance
- **One-time collection**: Completed offline assets requiring zero external network calls at render time.
- **Brand integrity**: Real logos downloaded from official corporate domains, brand kits, and verified public repositories (Wikimedia Commons, SimpleIcons, SVGL, and clearly identified public partner-logo asset libraries). No redraws, no AI image generation, no invented brands.
- **Security & Safety**:
  - **SVG assets**: Validated and sanitized to ensure no `<script>`, inline event handlers (`onclick`, `onload`, etc.), `<foreignObject>`, `<iframe>`, or external URL links.
  - **PNG assets**: Validated for genuine 8-bit alpha channel transparency; no opaque bounding boxes or forced bitmap background edits.
  - **File limits**: All assets are well below the 1 MB hard ceiling, with the majority kept under 50 KB.
- **Confidential employer roles**: For roles specifying recruitment agencies (e.g. Interaction, LHH Recruitment Solutions), the verified recruitment agency logo is preserved and explicitly tagged rather than fabricating a client mark.

## Directory Structure
```
company-logos/
├── cac40/                 # 40 official French CAC 40 companies (September 2026 composition)
├── logistics/             # 15 global and European logistics leaders
├── ai/                    # 15 leading AI research labs and technology providers
├── sustainability-saas/   # 15 sustainability, carbon accounting, and ESG SaaS platforms
├── shortlist/             # Active local CV application shortlist employers
├── hark-20261008/          # 35 additional Apply-company logos collected through Hark/Drive
├── collection-request.json
├── shortlist-companies.json
├── manifest.json          # Complete index with paths, sources, sha256 checksums, and status
└── README.md
```

## Coverage Summary
- **CAC 40**: **40 / 40 (100%)** verified (Accor, Air Liquide, Airbus, ArcelorMittal, AXA, BNP Paribas, Bouygues, Bureau Veritas, Capgemini, Carrefour, Crédit Agricole, Danone, Dassault Systèmes, Eiffage, Engie, EssilorLuxottica, Eurofins Scientific, Euronext, Hermès, Kering, L’Oréal, Legrand, LVMH, Michelin, Orange, Pernod Ricard, Publicis Groupe, Renault, Safran, Saint-Gobain, Sanofi, Schneider Electric, Société Générale, Stellantis, STMicroelectronics, Thales, TotalEnergies, Unibail-Rodamco-Westfield, Veolia, Vinci).
- **Logistics**: **15 / 15 (100%)** verified (DHL, UPS, FedEx, DSV, Kuehne+Nagel, CEVA Logistics, GEODIS, FM Logistic, XPO Logistics, GXO Logistics, Maersk, Nippon Express, CMA CGM, DB Schenker, STEF).
- **AI**: **15 / 15 (100%)** verified (OpenAI, Anthropic, Google DeepMind, Mistral AI, Hugging Face, Dataiku, Cohere, Perplexity AI, Databricks, NVIDIA, Microsoft, Meta, LightOn, Dust, Groq).
- **Sustainability SaaS**: **14 / 15 (93.3%)** verified (Greenly, Sweep, Plan A, Watershed, Persefoni, Normative, EcoVadis, Novisto, Workiva, Sphera, Worldfavor, CarbonChain, Position Green, Sami).
- **Shortlist Companies**: **19 / 21 (90.5%)** verified (EXTIME, Banque de France, CDEFI, Nexans, Musée Grévin, ECF Group, Interaction, Malakoff Humanis, ROCKWOOL/Rockfon, Fnac Darty, MASTERIS/SNCF, Stallergenes Greer, Actual group, Banque Palatine, Grant Thornton, Sciences Po, Eiffage, LHH, CCI Paris Île-de-France).

### Missing / Unverified Entries (Recorded honestly in `manifest.json`)
1. **Diligent** (`sustainability-saas`): No isolated public transparent brand kit vector/PNG found in public brand sources.
2. **European Camping Group (ECG)** (`shortlist`): Private corporate group holding with origin website guarded behind strict Cloudflare challenges; no authenticated transparent standalone SVG/PNG in public repositories.
3. **BEST VALUE (société à mission)** (`shortlist`): Small private French consulting entity with no verified transparent vector/PNG mark published; per instructions, unverified or generic substitute logos are not forced.

## Apply-company collection — 8 October 2026

The live Apply inventory contained 48 ready roles across 43 company labels. Hark collected original brand files in Drive; 35 additional SVG/transparent PNG logos passed the local library checks and HTTP/hash readback. Together with AXA, LVMH and Legrand, 38 of the 43 labels have a matching logo.

Remaining gaps: BPCE SA (current download opaque), Chambre de Métiers et de l’artisanat (only opaque JPG retrieved), Diptyque Paris and Ritz Paris (download access blocked), and Entreprise Confidentielle (unknown employer). The government initiative logo initially returned for Entreprises engagées was excluded and replaced with the consultancy’s own mark. Chronopost’s SVG with embedded raster assets was replaced with its official transparent PNG. Light artwork for SGD Pharma and Emeraude Solaire uses the existing dark-background rendering hint.

Collection evidence, original downloads, validation results and the complete company inventory are retained in `../../.local/qa/logo-intake-20261008/`. Original library rows and application/CV state were preserved. These are identification assets, not live-vacancy evidence.

## Trademarks & Intellectual Property
All company names, logos, and registered trademarks belong to their respective owners. These brand assets are stored solely for identification and presentation of employment history, partnerships, and applications within CV Studio.
