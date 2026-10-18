# Contributing to agricultural expansion

Updated 2026-10-18. The [vision](VISION.md) defines the destination; the
[platform plan](AGRICULTURE_PLATFORM_PLAN.md) defines milestones and acceptance gates.
[Epic #78](https://github.com/Rekin226/Agronaut/issues/78) tracks the work. The documentation
is published for review in [PR #242](https://github.com/Rekin226/Agronaut/pull/242).

## Choose a contribution

Start with [P01 source cases and vocabulary](https://github.com/Rekin226/Agronaut/issues/243).
This work is open to agronomists, irrigation specialists and documentation contributors.
Software contributors can review the contracts and migration design while the source baseline
is established. Later issues list implementation dependencies; source research and design
preparation can begin before all code dependencies land.

Comment on the relevant issue with the bounded part you want to take, then follow
[CONTRIBUTING.md](../CONTRIBUTING.md). Issues are unassigned. Prefer focused PRs over one
large change covering an entire milestone. Every issue includes skills, scope, starting files
and completion checks. None is labelled a good first issue merely because it is unassigned.

## Contributor issue map

| Work package | Issue | Depends on |
|---|---|---|
| P01 | [Define capability taxonomy and source-backed irrigation reference cases](https://github.com/Rekin226/Agronaut/issues/243) | Ready to start |
| P02 | [Add typed farm, production unit, cycle and observation models](https://github.com/Rekin226/Agronaut/issues/244) | [#243](https://github.com/Rekin226/Agronaut/issues/243) |
| P03 | [Persist farms and units with a reversible legacy-profile migration](https://github.com/Rekin226/Agronaut/issues/245) | [#244](https://github.com/Rekin226/Agronaut/issues/244) |
| P04 | [Introduce a capability registry and adapters for existing engines](https://github.com/Rekin226/Agronaut/issues/246) | [#243](https://github.com/Rekin226/Agronaut/issues/243), [#244](https://github.com/Rekin226/Agronaut/issues/244) |
| P05 | [Make consultations and remembered facts specific to the active production unit](https://github.com/Rekin226/Agronaut/issues/247) | [#245](https://github.com/Rekin226/Agronaut/issues/245), [#246](https://github.com/Rekin226/Agronaut/issues/246) |
| P06 | [Add validated weather records and offline imports for field irrigation](https://github.com/Rekin226/Agronaut/issues/248) | [#244](https://github.com/Rekin226/Agronaut/issues/244) |
| P07 | [Implement cited reference evapotranspiration and crop-water demand](https://github.com/Rekin226/Agronaut/issues/249) | [#243](https://github.com/Rekin226/Agronaut/issues/243), [#248](https://github.com/Rekin226/Agronaut/issues/248) |
| P08 | [Track root-zone water balance and derive irrigation requirements](https://github.com/Rekin226/Agronaut/issues/250) | [#249](https://github.com/Rekin226/Agronaut/issues/249) |
| P09 | [Design a bounded drip system with zones, flow, pressure and runtime](https://github.com/Rekin226/Agronaut/issues/251) | [#250](https://github.com/Rekin226/Agronaut/issues/250) |
| P10 | [Ship offline irrigation CLI commands and reproducible reports](https://github.com/Rekin226/Agronaut/issues/252) | [#251](https://github.com/Rekin226/Agronaut/issues/251) |
| P11 | [Connect irrigation tools, domain-scoped knowledge and the chat/web workflow](https://github.com/Rekin226/Agronaut/issues/253) | [#247](https://github.com/Rekin226/Agronaut/issues/247), [#252](https://github.com/Rekin226/Agronaut/issues/252) |
| P12 | [Run the drip pilot and publish a reproducible field-validation report](https://github.com/Rekin226/Agronaut/issues/254) | [#253](https://github.com/Rekin226/Agronaut/issues/253) |
| M3S | [Specify sprinkler calculations and independent validation cases](https://github.com/Rekin226/Agronaut/issues/255) | [#243](https://github.com/Rekin226/Agronaut/issues/243) |
| M3F | [Specify surface irrigation feasibility and measured scheduling requirements](https://github.com/Rekin226/Agronaut/issues/256) | [#243](https://github.com/Rekin226/Agronaut/issues/243) |

## Working order

P01 establishes the scientific and vocabulary baseline. P02 then enables persistence (P03),
tool routing (P04) and weather contracts (P06). P03 and P04 feed consultation work (P05),
while P06 leads through crop water (P07), soil state (P08), drip design (P09) and the offline
CLI/report (P10). P05 and P10 join in the complete chat/web workflow (P11); P12 evaluates
that workflow on farms.

The sprinkler and surface issues deliver reviewed specifications and evaluation cases for M3.
They do not imply that those systems are already implemented. Seasonal crop management,
whole-farm allocation and additional enterprises remain in M4–M6 of the plan; create their
implementation issues after the shared contracts and field evidence make the work concrete.

## Coordinate with existing work

- [#119](https://github.com/Rekin226/Agronaut/issues/119): existing design identity work.
- [#214](https://github.com/Rekin226/Agronaut/issues/214): per-field coefficient provenance.
- [#77](https://github.com/Rekin226/Agronaut/issues/77): knowledge corpus contributions.
- [#79](https://github.com/Rekin226/Agronaut/issues/79): offline-first capabilities.

Preserve existing aquaponic and hydroponic interfaces. Keep field calculations pure, sourced
and validated at their inputs. Mathematical correctness, workflow correctness and field
validation are separate acceptance gates. A pilot that fails to validate a method is useful
work and must be reported as such.
