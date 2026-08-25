# Economics and Costs

Aquaponics and hydroponics can produce a lot of food per square metre, but **profitability is
not automatic** — the peer-reviewed and practitioner consensus is that many operations are
not economically viable, and food-security claims are regularly overstated (FAO Yumina work;
Wiley review of aquaponics economics, 2025). Treat every design as a business case, not just
an engineering one.

## The main cost buckets

**Capital (one-time):**
- Tanks/reservoir, grow beds (raft/DWC or NFT), plumbing, and a sump.
- Pumps and aeration (air pump + stones) — and the power to run them 24/7.
- A greenhouse or shade/cover structure in most climates.
- Instrumentation: pH and EC meters at minimum.
- Fingerlings/seedlings and initial nutrient or feed stock.

**Operating (recurring):**
- **Energy** is usually the largest and most underestimated ongoing cost — pumps and
  aeration run continuously, plus heating/cooling to hold the operating temperature band.
- Feed (aquaponics) or nutrient salts (hydroponics).
- Water and makeup water (small in a recirculating system, but not zero).
- Labour — daily monitoring, feeding, harvesting, cleaning. Often the true largest cost once
  you price your own time.
- Replacements: fish losses, pump/impeller wear, seals, media.

## Why systems lose money

- **Energy and labour dwarf the value of the crop** at small scale. A few square metres of
  lettuce rarely pays for a pump running 8,760 hours a year plus daily attention.
- **Underpricing your labour.** Hobby math that ignores time looks profitable; commercial
  math that pays for it usually does not, until scale.
- **Single low-value crop.** Leafy greens have thin margins; high-value herbs (basil, mint)
  and the fish protein are where aquaponics economics improve.
- **Undersized or oversold.** A design sized past its water/energy budget bleeds cost; see
  Agronaut's water-budget feasibility check.

## What improves the economics

- **Scale** — fixed costs (your time, a controller, a greenhouse) amortise over more area.
- **High-value crops** and selling the fish, not just greens.
- **Cheap, reliable energy** — solar for pumps/aeration in off-grid and field settings
  changes the equation more than any other single lever.
- **Local, direct sales** (restaurants, markets) that capture the freshness premium.
- **Calibration** — matching stocking, feed, and grow area to what the system actually
  sustains avoids wasted feed, water, and materials.

## Priced numbers live in the price book, not here

Agronaut carries a **regional price book** (`data/price_book.json`) with researched
prices — value, range, currency, source, and an as-of date — for the components its
designs call for (tanks, pumps, aeration, pipe, media, rafts, envelope, fingerlings,
feed, electricity, water), currently for Burkina Faso / West Africa, Taiwan, and a
global/US baseline. The `estimate_system_cost` tool prices a sized design against it as
a range, names anything the book cannot price, and always says: these are researched
estimates to verify with local quotes, not quotes.

## Four things the price data says that contradict common assumptions

**The plants carry the profit, not the fish.** This is convergent across sources and
regions: SRAC-5006 found the fish portion unprofitable in nearly every study it reviewed,
and a SARE grower survey found **81% of revenue came from produce**. Design the fish side
to feed the plants and to be sold, not as the profit centre.

**Which market level you are quoting matters more than the number.** Farm-gate (what the
farmer receives), wholesale, and retail are different prices for the same fish, and the
ratio between them is not a constant: measured Taiwanese farm-gate to wholesale ratios run
**0.90 to 1.62**, so applying a fixed margin to a retail price to "get" farm-gate is
guesswork. Agronaut's price book labels every line with its market level for this reason.

**Smoking fish is not a revenue multiplier.** In West Africa smoked catfish trades far
above fresh per kilogram (roughly 2,800-3,500 XOF/kg wholesale), which looks like a large
premium until you account for the **65-72% weight loss** in smoking: about 3.3 kg of fresh
fish makes 1 kg of smoked. At those prices the smoked route returns roughly **1,050 XOF per
kg of fresh input**, less than selling the same fish fresh at a 2,000 XOF/kg farm-gate
price. The same pattern holds in Nigerian retail data, where smoked catfish fetches only
~1.1x fresh tilapia per kilogram against a 0.3x yield. Smoke for **shelf life and market
reach**, never as a price play.

**Selling direct is the biggest single revenue lever available to a small grower.** The
documented ladder for aquaponic produce runs commodity -> restaurant -> retail/direct at
roughly **3-5x** (UF/IFAS HS1252). Real farm comparisons show the same: growers selling at
farmers' markets realise roughly **1.9x on fish and 2.8x on produce** versus wholesale
(SARE GS13-125). A system that loses money at farm-gate prices can clear at direct prices
— and that is a marketing and logistics problem, not an engineering one.

## How to think about feasibility

Before building, estimate: annual energy cost (pump + aeration + climate control), annual
feed/nutrient cost, realistic yield × a price you can actually get, and the value of your own
labour. If the crop value does not clear energy + labour, the design is a hobby, not a
business — which is fine if that is the goal, but say so honestly.

_Sources: FAO Fisheries & Aquaculture Technical Paper 589; FAO Yumina/aquaponics economics
notes; Wiley aquaponics economics review (2025); practitioner cost surveys._
