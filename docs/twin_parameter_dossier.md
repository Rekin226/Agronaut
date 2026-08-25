# Twin parameter dossier — cited values for the production twin

Compiled 2026-08-26 for the digital-twin epics (#85, #26) as the evidence base behind
`aqua_model/fishgrowth.py`, `cropgrowth.py`, `climate.py` and the biofilter coefficients.
Every number is tagged with how it was verified: [full text] = document read;
[abstract] = from the paper's abstract; [snippet] = verbatim in a search excerpt, page
not read in full; [derived] = arithmetic on cited numbers. Honest gaps are listed at the
end — do not fabricate them in code.

## 1. Fish bioenergetics / growth

### 1.1 TGC model form

- **TGC = 1000 · (W_f^(1/3) − W_i^(1/3)) / Σ(T · Δt)**, W in g, T in °C, t in days; prediction: W_f = (W_i^(1/3) + TGC/1000 · Σ T·Δt)^3. Exactly this form (with the ×1000 scaling) is stated in Mengistu et al. (2019) [full text] and Márquez et al. (2024) [full text].
- Origin of the thermal-unit growth coefficient: Cho, C.Y. & Bureau, D.P. (1998) "Development of bioenergetic models and the Fish-PrFEQ software to estimate production, feeding ration and waste output in aquaculture", *Aquatic Living Resources* 11(4), DOI 10.1016/s0990-7440(98)89002-5 [existence verified via OpenAlex].
- **Caution**: Jobling, M. (2003) "The thermal growth coefficient (TGC) model of fish growth: a cautionary note", *Aquaculture Research* 34 (DOI 10.1046/j.1365-2109.2003.00859.x): TGC is only valid in the temperature range where growth increases roughly linearly with temperature; it fails near and above the optimum. [existence verified; the caution is the paper's well-known thesis, also restated in Mengistu 2019 which adopts the model citing Jobling]
- Weight-dependence: Dumas, A., France, J. & Bureau, D.P. (2007) "Evidence of three growth stanzas in rainbow trout (Oncorhynchus mykiss) across life stages and adaptation of the thermal-unit growth coefficient", *Aquaculture* (DOI 10.1016/j.aquaculture.2007.01.041): TGC is approximately constant only within a growth stanza; for rainbow trout the stanzas are <20 g, 20–500 g, >500 g. [abstract/snippet; the specific per-stanza TGC numbers are paywalled and I could not verify them, so do not hard-code them]

### 1.2 TGC values (×1000 convention, g^(1/3) °C⁻¹ d⁻¹)

| Species | TGC value/range | Conditions | Source | Level |
|---|---|---|---|---|
| Cultured juvenile fish (generic envelope) | 0.1–3.2 | across species/studies (SGR 0.9–5.0 %/d) | Márquez et al. 2024, *Frontiers in Marine Science* 9:1332912 | full text |
| Nile tilapia | TGC = 0.611 + 0.01 × feeding rate (% BW/d); i.e. ≈0.63–0.7 typical, up to ~1.2 at high rations | meta-regression over 32 studies (mostly ponds); DO from 1→11.1 mg/L raises TGC by 88 %, pH 6.42→8.2 by 52 % | Mengistu, S.B., Mulder, H.A., Benzie, J., Komen, H. (2019) "A systematic literature review of the major factors causing yield gap ... in Nile tilapia", *Reviews in Aquaculture* 12:524–541 | full text |
| Nile tilapia (sanity check) | ≈0.8–1.1 | 50→600 g in 6–8 mo at ~28 °C (FAO growth figure) | computed from FAO 589 growth data | derived |
| Common carp | 0.37 (highest, at 20 °C); no significant growth difference 20–30 °C | juveniles ~18–24 g, lab, 3 % BW/d feeding | ALkhafaji, S.B. et al. (2023) "Implication of Thermal-Unit Growth Coefficient of the Common Carp (Cyprinus carpio L.) in Different Water Temperatures", *Egyptian Journal of Aquatic Biology and Fisheries* 27(4), DOI 10.21608/ejabf.2023.309600 | abstract (reconstructed from OpenAlex, full sentences read) |
| Rainbow trout | three-stanza structure (<20, 20–500, >500 g); per-stanza values not verified | | Dumas et al. 2007 | abstract |
| Atlantic salmon (calibration anchor) | industry ≈2.4–2.5; recent studies mean ≈2.7; RAS studies 2.18–2.45 | seawater/RAS grow-out | quoted in search results attributed to a PMC review (PMC11934803) and salmon RAS papers | snippet, moderate confidence |
| African catfish | no verified TGC found; use FCR/temperature data below | | | |

### 1.3 Temperature response (tolerance, optimum, cessation)

All rows below from **Somerville, C., Cohen, M., Pantanella, E., Stankus, A. & Lovatelli, A. (2014) "Small-scale aquaponic food production. Integrated fish and plant farming", FAO Fisheries and Aquaculture Technical Paper No. 589, FAO, Rome** [full text, PDF read], except where noted.

| Species | Vital range (°C) | Optimal growth (°C) | Growth cessation | Feed protein % | Growth rate |
|---|---|---|---|---|---|
| Nile tilapia | 14–36 | 27–30 | "do not feed or grow below 17 °C" (FAO 589, verbatim) | 28–32 | 600 g in 6–8 mo (50→500 g in ~6 mo ideal) |
| Common carp | 4–34 | 25–30 | survives to 4 °C (growth effectively nil at low T) | 30–38 | 600 g in 9–11 mo |
| Channel catfish | 5–34 | 24–30 | | 25–36 | 400 g in 9–10 mo |
| Rainbow trout | 10–18 | 14–16 | | 42 | 1000 g in 14–16 mo |
| African catfish (C. gariepinus) | | 26–32, ~30 °C established optimum (Kasihmuddin et al. 2021, *Animals* 11(12):3497) [full text via PMC] | "growth stops below 20–22 °C" (FAO 589, verbatim) | | |

Ammonia/nitrite/DO tolerances (FAO 589 fish table): tilapia NH3 <2, NO2 <1, DO >4 mg/L; trout NH3 <0.5, NO2 <0.3, DO >6 mg/L; carp and catfish NH3 <1, NO2 <1, DO >4 (>3 catfish) mg/L.

### 1.4 FCR by life stage / conditions

| Species / stage | FCR | Source | Level |
|---|---|---|---|
| Tilapia, grow-out in UVI-style aquaponics (RAS) | 1.0–1.7, mean ≈1.4 | Al-Hafedh, Y.S., Alam, A., Beltagi, M.S. (2008) "Food Production and Water Conservation in a Recirculating Aquaponic System...", *Journal of the World Aquaculture Society* 39(4):510–520 | full text |
| Tilapia, FCR response surface | FCR = 32.4 + 0.003·SW − 0.029·CP − 0.102·DO − 1.99·T + 0.034·T² (SW g, CP %, DO mg/L, T °C); minimum near T ≈ 29 °C [derived from the quadratic]; raising DO min→max improves FCR ~50 %, T 20→29.5 °C ~68 % | Mengistu et al. 2019 | full text |
| African catfish fingerlings, 26/28/30/32 °C | 2.01 / 1.79 / 1.72 / 1.64 | Kasihmuddin, Ghaffar & Das 2021, *Animals* 11:3497 | full text |
| Common carp, juveniles 20 °C lab | 3.94 | ALkhafaji et al. 2023 | abstract |
| Generic grow-out feeding rate (fish >50 g) | 1–2 % body weight/day | FAO 589 | full text |

Note: fry/fingerlings eat a higher %BW/day and convert better (FCR <1 is common for juvenile trout/catfish in RAS); I did not find a clean per-life-stage FCR table in a verifiable source, so the twin should treat FCR as stage- and temperature-dependent using the Mengistu response plus the species means above.

### 1.5 Feed protein → TAN excretion (the load-bearing formula)

- **P_TAN = F × PC × 0.092** where P_TAN = production rate of total ammonia nitrogen (kg/day), F = feed rate (kg/day), PC = protein concentration of feed (decimal fraction). Stated as Eq. (1) in **Ebeling, J.M., Timmons, M.B. & Bisogni, J.J. (2006) "Engineering analysis of the stoichiometry of photoautotrophic, autotrophic, and heterotrophic removal of ammonia-nitrogen in aquaculture systems", *Aquaculture* 257:346–358**, which itself cites Timmons et al. (2002) *Recirculating Aquaculture Systems*, 2nd ed. [snippet, but the equation and term definitions were shown verbatim in the ScienceDirect search excerpt: "P_TAN = F ⁎ PC ⁎ 0.092 where: PTAN Production rate of total ammonia nitrogen, (kg/day). F Feed..."]
- **Derivation of 0.092** (Timmons & Ebeling, *Recirculating Aquaculture*, 2nd ed., 2010): 0.092 = 0.16 × 0.80 × 0.80 × 0.90, i.e. protein is 16 % N; 80 % N assimilated; 80 % of assimilated N excreted; 90 % of excreted N is TAN [snippet from a copy of the book: "0.092 = .16 ⋅ .80 x .80 ⋅ .90"]. A recent aquaponics design paper restates it as "0.092 is the average percent of the feed mass excreted as ammonia", citing Timmons et al. (2018, 4th ed.): Tetreault, J., Fogle, R.L. & Guerdat, T. (2023), *Frontiers in Sustainable Food Systems* 7:1059066 [full text extract].
- Worked value: 32 % protein feed gives 0.092 × 0.32 ≈ **29.4 g TAN per kg feed** [derived].
- **Independent FAO nitrogen budget** (FAO 589, Appendix 4, verbatim logic): ~30 % of feed protein N retained in fish; 70 % lost (15 % as solids/uneaten, 55 % excreted as ammonia or degradable products); with ~60 % of solids removed by clarifiers, ~61 % of feed N ends up as ammonia; NH3 = N × 1.2. Example: 200 g feed at 32 % protein → **≈7.5 g ammonia (NH3)/day** (≈37 g NH3/kg feed ≈ 31 g N/kg feed). [full text] This is the "30–40 % of protein N excreted as TAN" convention: FAO's 55 % excreted fraction and Timmons' 0.80×0.80×0.90 = 57.6 % of feed N excreted as TAN bracket the same physiology.

---

## 2. Nitrification

### 2.1 Stoichiometry (per g TAN oxidized to NO3-N)

| Quantity | Value | Source | Level |
|---|---|---|---|
| O2 consumed | 4.57 g O2/g TAN | Timmons & Ebeling (2010) *Recirculating Aquaculture* 2nd ed., as quoted in ICAR-CMFRI Training Manual No. 28 (2022), ch. 4, and multiple RAS papers | snippet, consistent across ≥2 sources |
| Alkalinity consumed | 7.07–7.14 g as CaCO3/g TAN | same sources ("7.14 g alkalinity as CaCO3" per Timmons & Ebeling 2010) | snippet |
| Full engineering stoichiometry (incl. biomass yields, heterotrophic pathway) | see Ebeling, Timmons & Bisogni 2006, *Aquaculture* 257:346–358 | existence verified; paywalled | |

### 2.2 Areal rates for moving-bed media — Rusten et al. 2006 [full text, entire paper read]

**Rusten, B., Eikebrokk, B., Ulgenes, Y., Lygren, E. (2006) "Design and operations of the Kaldnes moving bed biofilm reactors", *Aquacultural Engineering* 34:322–331.**

| Parameter | Value | Conditions |
|---|---|---|
| Rate model | r_N = k·(S_N)^n, n = 0.7 (Hem et al. 1994); k = 0.50 fitted at a turbot RAS (r_N in g NH4-N/m²/d, S_N in mg NH4-N/L, 15 °C-normalized) | TAN-limited regime |
| Max design-curve rate | 1.0 g NH4-N/m²/d at DO ≈ 5 mg/L and organic load 1 g BOD5/m²/d, 15 °C, excess TAN; same rate needs DO ≈ 8 mg/L at 3 g BOD5/m²/d | Fig. 3, from Hem et al. 1994 |
| DO/TAN transition | oxygen becomes limiting when DO:TAN < 3.2 (mg/mg) (Szwerinski et al. 1986); fish farms usually run TAN < 1 mg N/L, so TAN is rate-limiting |
| Temperature correction | k_T2 = k_T1 · θ^(T2−T1), **θ = 1.09** (Rusten et al. 1995a) |
| pH/alkalinity | nitrification rate at pH 6.7 is ~50 % of rate at pH 7.3; rate falls with alkalinity below ~1–2 mmol/L; thin biofilms maintain max rates down to 0.7 mmol/L alkalinity |
| Observed rates, salmon smolt (12–13 °C) | 0.1 g/m²/d after 2 weeks start-up → 0.4–0.5 g/m²/d after ~125 days |
| Observed rates, trout/char (9 °C) | max 0.30 g NH4-N/m²/d at 0.45 g/m²/d TAN load |
| Marine penalty | seawater rates ≈ 60 % of freshwater |
| Kaldnes K1 media | 9.1 mm diameter × 7.2 mm; specific biofilm area 500 m²/m³ (bulk), 300 m²/m³ at 60 % fill; recommended filling fraction < 70 %; "active biofilm surface area of up to 350 m²/m³ reactor volume" |

### 2.3 Volumetric TAN conversion rate (VTR)

| Filter type | VTR (g TAN/m³ media/day) | Conditions | Source | Level |
|---|---|---|---|---|
| Moving bed (MBBR) | **267 ± 123** | commercial-scale tilapia RAS, warm water, NC State Fish Barn | Guerdat, T.C., Losordo, T.M., Classen, J.J., Osborne, J.A., DeLong, D.P. (2010) "An evaluation of commercially available biological filters for recirculating aquaculture systems", *Aquacultural Engineering* (S0144860909000880) | snippet (numbers quoted verbatim in search result), high confidence |
| Floating bead | 586 ± 284 | same study | same | snippet |
| Fluidized sand | 667 ± 344 | same study | same | snippet |
| MBBR cross-check from areal rates | ≈135 (cold water, 0.45 g/m²/d × 300 m²/m³) to ≈350 (warm, 1.0 g/m²/d × 350 m²/m³) | | Rusten et al. 2006 areal rates × K1 areas | derived |
| Design placeholder for small systems | areal 0.2–2 g NH3/m²/d; **conservative design value 0.57 g NH3/m² media surface/day** | | FAO 589, Appendix 4 | full text |

FAO 589 media sizing table (full text): volcanic tuff/gravel SSA 300 m²/m³ → 1 L media processes ammonia from 4.5 g feed/day (22.2 L media per 100 g feed/day); Bioballs 600 m²/m³ → 9.0 g feed/L; sand 5000 m²/m³ → 75 g feed/L; LECA 200–250 m²/m³ → 3.0–3.8 g feed/L.

### 2.4 Environmental corrections

- Temperature: θ = 1.09 per °C (Rusten et al. 2006 [full text]); nitrifier tolerance range 17–34 °C, "if the water temperature drops below 17 °C, bacteria productivity will decrease" (FAO 589 [full text]).
- pH: tolerance 6–8.5; FAO recommends operating aquaponics at pH 6–7 as a fish/plant/bacteria compromise (FAO 589 [full text]); rate roughly halves from pH 7.3 to 6.7 (Rusten et al. 2006 [full text]).
- Further kinetics reference (pH, DO, organics, TAN): Chen, S., Ling, J., Blancheton, J.-P. (2006) "Nitrification kinetics of biofilm as affected by water quality factors", *Aquacultural Engineering* 34:179–197 [existence verified via Semantic Scholar; content paywalled].
- Startup/nitrite: NO2 peaks appear if TAN load rises quickly; nitrite spikes observed when pH dropped suddenly (Rusten et al. 2006 [full text]).

---

## 3. Plant growth

### 3.1 Van Henten (1994) lettuce model

Citation: **van Henten, E.J. (1994) "Validation of a dynamic lettuce growth model for greenhouse climate control", *Agricultural Systems* 45(1):55–72** (citation format verified via a citing paper's reference: "Agric. Sys. 45, pp. 55–72"); also van Henten's PhD thesis "Greenhouse climate management: an optimal control approach", Wageningen, 1994, open access at edepot.wur.nl/205106.

State equations (two independent reproductions agree: (a) Frontiers in Plant Science 2023, PMC10286798, "Incorporating the effect of the photon spectrum on biomass accumulation of lettuce using a dynamic growth model" [full text extract]; (b) MarekWadinger/ecompc-greenhouse-platform `core/lettuce_model.py` [full code read]):

- dX_sdw/dt = r_gr · X_sdw  (structural dry weight, g m⁻²)
- dX_nsdw/dt = c_α · f_phot − r_gr·X_sdw − f_resp − ((1−c_β)/c_β) · r_gr · X_sdw  (non-structural dry weight)
- r_gr = c_gr,max · X_nsdw/(c_γ·X_sdw + X_nsdw) · c_Q10,gr^((T−20)/10)
- f_resp = (c_resp,sht·(1−c_τ) + c_resp,rt·c_τ) · X_sdw · c_Q10,resp^((T−25)/10)
- f_phot: canopy closure (1 − exp(−c_K·c_lar·(1−c_τ)·X_sdw)) times a leaf photosynthesis rate that saturates between light term c_ε·I and CO2 term (carboxylation conductance g_car = c_car1·T² + c_car2·T + c_car3 in series with boundary and stomatal conductances), CO2 compensation point Γ = c_Γ·c_Q10,Γ^((T−20)/10).

| Symbol | Meaning | Value | Units | Notes |
|---|---|---|---|---|
| c_α | CO2 → CH2O conversion | 0.68 (= 30/44) | g/g | both sources |
| c_β | yield factor (growth respiration) | 0.8 | – | both |
| c_gr,max | saturation growth rate at 20 °C | 5×10⁻⁶ | s⁻¹ | both |
| c_γ | growth-rate coefficient | 1.0 (PMC paper) / 1.1981 (GitHub impl.) | – | flag: use 1.0 for the original |
| c_Q10,gr | growth Q10 | 1.6 | – | both |
| c_resp,sht | shoot maintenance respiration at 25 °C | 3.47×10⁻⁷ | s⁻¹ | both |
| c_resp,rt | root maintenance respiration at 25 °C | 1.16×10⁻⁷ | s⁻¹ | both |
| c_Q10,resp | respiration Q10 | 2.0 | – | both |
| c_τ | root dry-mass fraction | 0.15 | – | GitHub impl. |
| c_K | canopy extinction coefficient | 0.9 | – | both |
| c_lar | structural leaf area ratio | 75×10⁻³ | m²/g | both |
| c_ε | light use efficiency at high CO2 | 17×10⁻⁶ | g CO2 / J (PAR) | both |
| c_Γ | CO2 compensation point at 20 °C | 40 | ppm (vpm) | both; Q10 = 2 |
| c_ω | CO2 ppm→density conversion | 1.83×10⁻³ | g m⁻³ per ppm (15 °C, 101.3 kPa) | GitHub impl. |
| g_bnd | boundary layer conductance | 7.2×10⁻⁴ | m s⁻¹ | GitHub impl. |
| g_stm | stomatal conductance | 5×10⁻³ | m s⁻¹ | GitHub impl. |
| c_car1/2/3 | carboxylation-conductance polynomial | −1.32×10⁻⁵ / 5.94×10⁻⁴ / −2.64×10⁻³ | m s⁻¹ °C⁻², m s⁻¹ °C⁻¹, m s⁻¹ | both |
| DM:FM | lettuce dry:fresh mass | ≈0.05–0.10 (impl. uses 0.10) | – | implementation convention, not a van Henten parameter |

### 3.2 Simpler light-use (RUE) alternatives

| Item | Value | Source | Level |
|---|---|---|---|
| Lettuce RUE (field) | ≈1.4 g DM/MJ absorbed PAR reported in a search extraction; lettuce had the **lowest** RUE of lettuce/onion/red beet due to respiration cost of high leaf area (abstract, solid) | Tei, F., Scaife, A., Aikman, D.P. (1996) "Growth of Lettuce, Onion, and Red Beet. 1. Growth Analysis, Light Interception, and Radiation Use Efficiency", *Annals of Botany* 78:633–643 | value: snippet, low-moderate confidence; ranking statement: abstract |
| Lettuce light use in van Henten model | c_ε = 17×10⁻⁶ g CO2 J⁻¹ PAR at saturating CO2 (≈17 g CO2/MJ ≈ 11.6 g CH2O/MJ) | van Henten 1994 parameters above | full text (reproductions) |
| Companion modeling paper | Tei et al. (1996) part 2, "Growth Modelling", *Annals of Botany* 78:645– (expolinear/day-degree models) | verified via OpenAlex | |

### 3.3 Nitrogen uptake

| Item | Value | Source | Level |
|---|---|---|---|
| Lettuce N assimilation (DWC, 3-phase) | **0.01837 g N per plant per day** | Tetreault et al. 2023 (Front. Sust. Food Syst. 7:1059066), citing Rakocy & Ebeling (2018), ch. in Timmons et al., *Recirculating Aquaculture* 4th ed., pp. 663–707 | full text extract |
| Per-head / per-kg conversion | ≈0.64 g N per head over a 35-d cycle; at 250–400 g/head ≈ 1.6–2.6 g N per kg fresh biomass | arithmetic on the above + FAO 589 head mass | derived |
| Hydroponic lettuce solution N | NO3 8.9–10.6 mmol/L + NH4 0.5–1.0 mmol/L (Dutch standards) | De Kreij et al. (1999), reproduced in Maucieri et al., ch. 4 of Goddek et al. (2019), Table 4.4 | full text (book PDF) |

### 3.4 Yields, cycle length, EC/pH

| Crop | Density | Cycle | pH | EC (dS/m) | Source |
|---|---|---|---|---|---|
| Lettuce | 20–25 heads/m² (18–30 cm spacing) | 24–32 days transplant→harvest; market weight 250–400 g/head | 6.0–7.0 (ideal 5.8–6.2); temp 15–22 °C, bolts >24 °C air / >26 °C water | 1.2–1.7 (De Kreij, DFT) | FAO 589 [full text]; De Kreij et al. 1999 via Goddek book Table 4.4 [full text] |
| Lettuce annual yield (hydroponic greenhouse) | | **41 ± 6.1 kg/m²/yr**, water use 20 ± 3.8 L/kg | | | Barbosa, G. et al. (2015), *Int. J. Environ. Res. Public Health* 12(6):6879 [abstract, numbers in abstract] |
| Basil | 8–40 plants/m² (15–25 cm) | 5–6 weeks to first harvest, then 30–50 days of picking | 5.5–6.5; optimal 20–25 °C | NFT trials ran EC 0.5–4.0 (Walters & Currey 2018, HortScience 53(9):1319) | FAO 589 [full text]; Walters & Currey [abstract] |
| Tomato | 3–5 plants/m² (40–60 cm) | 50–70 d to first harvest; 8–10 months indeterminate | 5.5–6.5; opt. 13–16 °C night / 22–26 °C day; growth stops <8–10 °C; floral abortion >40 °C | 2.6 (generative, stone wool, De Kreij) | FAO 589 [full text]; De Kreij via Goddek book [full text] |
| Cucumber / pepper (bonus) | | | 5.5 / 5.6 | 3.2 / 2.5–3.0 | De Kreij via Goddek book [full text] |

---

## 4. Aquaponics design ratios

### 4.1 Rakocy / UVI

| Parameter | Value | Source | Level |
|---|---|---|---|
| Feeding rate ratio, raft | **"The optimum feeding rate ratio for raft aquaponics ranges from 60 to 100 g/m²/day"** (verbatim) | Rakocy, J.E., Masser, M.P., Losordo, T.M. (2006) "Recirculating Aquaculture Tank Production Systems: Aquaponics — Integrating Fish and Plant Culture", SRAC Publication No. 454 | snippet (verbatim from the PDF's own text in search index), high confidence |
| Lettuce-specific ratio | 57 g feed/day per m² lettuce | Rakocy (2012), ch. in Tidwell (ed.) *Aquaculture Production Systems*, as reported in Palm et al., ch. 7 of Goddek et al. 2019 | full text (book PDF) |
| Media-bed component ratio | 1 m³ fish-rearing tank : 2 m³ pea gravel (3–6 cm), supports 60 kg/m³ tilapia | same | full text |
| Plant:fish area ratio | at least 7:3 plant growing area : fish surface area | same | full text |
| UVI layout ratio | fish tanks : filters : plant area = 2 : 1 : 5 | Khandaker & Kotzen (2018), reproduced in Kotzen et al., ch. 12 of Goddek et al. 2019, Fig. 12.2 | full text |
| UVI-design validation | ratios 56–169 g/m²/d tested; 13-month tilapia net production 32–44.3 kg/m³, FCR 1.0–1.7 | Al-Hafedh et al. 2008, JWAS 39(4):510–520 | full text |

### 4.2 FAO 589 (Somerville et al. 2014) — all [full text]

| Parameter | Value |
|---|---|
| Feed rate ratio, leafy greens | **40–50 g feed/m²/day** |
| Feed rate ratio, fruiting vegetables | **50–80 g feed/m²/day** (assumes 32 % protein feed) |
| Plant density | 20–25 plants/m² leafy; 4–8 plants/m² fruiting |
| Fish feeding | 1–2 % of body weight/day (fish >50 g) |
| Stocking density | **10–20 kg/m³** standard for the manual's systems; 1–5 kg/m³ low-density variant; media beds clog above ~15 kg/m³ without added filtration |
| Water flow | cycle total volume 2×/h at high density, 1×/h at low density |
| DWC canal | depth 30 cm; retention time 1–4 h (summary: 2–4 h); air stones ~4 L/min each every 2–4 m² of canal |
| NFT | 1–2 L/min per grow pipe |
| Media bed | depth ~30 cm; inert media, high SSA (see 2.3) |
| Aeration (small unit, ~1000 L) | 4–8 L/min total via ≥2 air stones in fish tank + 1 in biofilter |
| Biofilter design rate | 0.57 g NH3/m² media/day (conservative) |
| Water quality targets | nitrifiers 17–34 °C, pH 6–8.5 (operate 6–7); NO3 kept <150 mg/L (exchange water above); ammonia/nitrite <1 mg/L alarm |
| Worked example | 25 lettuce/week ↔ 10–20 kg fish, 200 g feed/day, 4 m² growing area |

### 4.3 Coupled vs decoupled (Goddek et al.)

- Goddek, S., Delaide, B., Mankasingh, U., Ragnarsdottir, K.V., Jijakli, H., Thorarinsdottir, R. (2015) "Challenges of Sustainable and Commercial Aquaponics", *Sustainability* 7(4):4199 [existence + OA verified] — framing reference for coupled vs decoupled.
- Goddek, S., Joyce, A., Kotzen, B., Burnell, G.M., eds. (2019) *Aquaponics Food Production Systems*, Springer (open access, DOI 10.1007/978-3-030-15943-6) [full text]:
  - Ch. 7 (Palm et al.): coupled-system ratio knowledge is limited; UVI ratios above are the anchor; plants need e.g. K 230–400 mg/L that fish water alone cannot supply.
  - Ch. 8 (Goddek et al.): decoupled systems are sized by nutrient/water mass balance, not a fixed ratio: φ_RAS + φ_MIN − φ_HP = 0 (Eq. 8.4) with flows solved via Eqs. 8.5–8.9 (distillation/demineralization loop); sizing is driven by crop evapotranspiration.
  - Ch. 5 (Lennard & Goddek): alternate ratio findings, e.g. Endut et al. (2010): 15–42 g/m²/day for African catfish + water spinach; Lennard (2017): as low as <11 g/m²/day for some leafy greens when solids are remineralized.

---

## 5. System-type engineering specs

Primary sources: FAO 589 [full text]; Goddek et al. 2019 book chs. 7 and 12 [full text]; industry/extension sources flagged.

| System | Plant density | Water/flow | Aeration | Crops | Pros/cons (source language) |
|---|---|---|---|---|---|
| **Media bed** | 20–25/m² leafy, 4–8/m² fruiting (FAO) | bed depth 30 cm; flood-and-drain; system turnover 1–2×/h; 1 m³ tank : 2 m³ gravel (Rakocy) | wet/dry interface supplies O2 (FAO) | widest range incl. fruiting, root veg via attached wicking bed (FAO) | + combined mechanical/biofiltration; − clogging >15 kg/m³, weight, cost of media (FAO) |
| **NFT** | 20–25/m² leafy in channels (FAO density guidance) | **1–2 L/min per channel**; separate clarifier + biofilter required (FAO) | aeration into biofilter (FAO) | leafy greens, herbs, strawberries (FAO) | + light, vertical-friendly, water-efficient; − pump failure dries roots fast, nutrient film depletes in long pipes (FAO) |
| **DWC/raft** | 20–25 heads/m² (FAO); UVI standard | canal depth 30 cm; retention 1–4 h; large buffer volume (FAO) | air stones every 2–4 m² at ~4 L/min, or Venturi injectors; Kratky air gap 3–4 cm (FAO) | lettuce, basil, leafy greens (FAO/UVI) | + thermal/chemical buffering, highest commercial track record (UVI); − needs most aeration, root-zone DO critical (FAO) |
| **Vertical towers / ZipGrow** | ~2× horizontal density per footprint; "conservative estimate... at least double... to 64 plants/m²"; ZipGrow row spacing ~0.5 m | NFT-type drip through media in towers | as NFT | leafy greens, herbs | + footprint yield; − lighting complexity on vertical faces; >4 stacked tiers unprofitable and labour +25 % when scissor lifts needed (Storey 2015) — all from Kotzen et al., ch. 12 of Goddek et al. 2019 [full text]; Touliatos et al. 2016 for vertical-vs-horizontal lettuce comparison |
| **Wicking bed** | root vegetables (onion, carrot, beet, radish, taro) attached to media systems (FAO 589) | sub-irrigated reservoir, capillary rise; WUE equal or better than precision surface irrigation for tomato (Semananda, N.P.K., Ward, J.D., Myers, B.R. 2016, "Evaluating the Efficiency of Wicking Bed Irrigation Systems for Small-Scale Urban Agriculture", *Horticulturae* 2(4)) [snippet/abstract] | none needed | root veg, tomato | + very low water use, no pumps; − not a nutrient-removal component for the fish loop |
| **Dutch/Bato bucket** | 2 plants/bucket; rows ~1.8 m (6 ft) apart, 0.4–0.45 m in-row (≈2.2–2.7 plants/m²); hydroponic tomato/cucumber row width 0.9–1.2 m (Badgery-Parker & James 2010, via Goddek book ch. 12 [full text]) | drip-fed, shared drain line, media (perlite/LECA) | none beyond root-zone drainage | tomato, cucumber, pepper, eggplant (vining fruiting crops) | + simple modular fruiting-crop culture; − per-plant drippers to clog, solids must be well filtered. Industry sources: CropKing/GrowSpan guides [snippet] |
| **Hybrid** | combine media bed (as filter) with DWC/NFT rafts; FAO 589 presents all three methods sharing the same fish/filtration ratios; sizing via feed rate ratio applies to total plant area [full text] |

---

## 6. Greenhouse

### 6.1 Simple energy balance

Conductive/convective loss Q = U·A·(T_in − T_out), plus infiltration ~0.5–1.5 air changes/h for film houses (infiltration value not verified; keep as tunable).

Glazing table from University of Arkansas greenhouse course, Unit 3 "Glazing" (values "averages developed from various company sources") [full text of page]:

| Glazing | Light transmittance (%) | U (Btu ft⁻² h⁻¹ °F⁻¹) | U (W m⁻² K⁻¹) [derived ×5.678] |
|---|---|---|---|
| Single glass 3 mm | 90 | 1.05 | 5.96 |
| Double-strength glass | 88 | 1.1 | 6.25 |
| Insulated (double) glass | 78 | 0.70 | 3.97 |
| Single polyethylene | 85 | 1.2 | 6.81 |
| Double polyethylene | 77 | 0.70 | 3.97 |
| Acrylic twin-wall 8 mm | 84 | 0.56 | 3.18 |
| Polycarbonate twin-wall 8 mm | 80 | 0.56 | 3.18 |

Cross-check (Bartok, "Determining greenhouse heat loss", *Greenhouse Management*): U ≈ 1.15 single glass/poly/PC, 0.7 double poly, 0.6 double-wall PC/acrylic [snippet]. The two sources agree within ~10 %.

### 6.2 Ventilation rules of thumb

- Summer fan ventilation: **~1 air change per minute**; NGMA design standard **8 cfm per ft² of floor area** (≈ 2.44 m³ min⁻¹ m⁻², ≈ 146 m³ h⁻¹ m⁻²); 7 cfm/ft² if thermal/shade screens are used. Sources: Greenhouse Management "Forced-Air Ventilation" and GGS/nursery industry guides reporting the NGMA standard [snippet, consistent across ≥3 sources].

### 6.3 Evapotranspiration (water loss)

- Reference: **FAO-56 Penman-Monteith** (Allen, R.G., Pereira, L.S., Raes, D., Smith, M. 1998, *Crop evapotranspiration*, FAO Irrigation and Drainage Paper 56, Eq. 6), reference surface: grass 0.12 m, surface resistance 70 s/m, albedo 0.23; ET0 in mm/day from Rn, G, T, u2, es−ea, Δ, γ [full text of FAO chapter page].
- Greenhouse-simplified form (Katsoulas, N. & Kittas, C. 2011, "Greenhouse Crop Transpiration Modelling", InTech, ch. read in full): **λE = A·G + B·D** (G = solar radiation W/m², D = VPD kPa), with **A = A0·(1 − e^(−k·LAI))**, k = 0.64 for tomato (Stanghellini 1987), and **B = B0·LAI** (Baille et al. 1994); A and B are crop-specific regression constants; a summary table of A/B by species is given in Seginer (1997) [full text]. I did not verify specific A0/B0 numbers, so leave them as calibration parameters.
- Practical magnitude for lettuce: 20 ± 3.8 L water/kg produce at 41 kg/m²/yr → ≈ 2.2 L m⁻² d⁻¹ annual average greenhouse water demand (Barbosa et al. 2015 [abstract] + arithmetic [derived]).
- Aquaponics-side anchor: daily top-up in the UVI-type system covers sludge, evaporation and transpiration; Al-Hafedh 2008 reported ~1.4 % of volume/day [full text, partial: the paper states daily compensation; the 1.4 % figure I did not re-verify, omit from code].

---

## Honest gaps (do not fabricate in code)
- Per-stanza TGC values for rainbow trout (Dumas et al. 2007) and a species-specific TGC for African catfish were not extractable from open sources; use the generic envelope (0.1–3.2), the tilapia regression, and salmon anchors, and flag trout/catfish TGC as calibration targets.
- Tei (1996) lettuce RUE number is low-confidence (search extraction); the Van Henten c_ε is the safer light-use constant.
- Ebeling et al. (2006) per-pathway stoichiometric table (biomass yields, base consumption for heterotrophic/photoautotrophic pathways) is paywalled; the 4.57 g O2 and 7.07–7.14 g CaCO3 per g TAN figures rest on secondary quotations of Timmons & Ebeling (2010).