# Field reference data retained for research screening

These are unmodified numeric files and documentation, downloaded on 2026-10-20.
They are not records from an Agronaut pilot and do not establish model validity.
The adjacent [registry](../field_dataset_registry.json) retains download URLs, versions,
file sizes, publisher MD5 checksums, local SHA-256 hashes, citations and limitations.

| Files | Attribution | License |
|---|---|---|
| `burkina_soil_2018.tsv`, `burkina_soil_dictionary.tsv`, `burkina_soil_methods.pdf` | Barro, Mariam; Wonni, Issa; Tollenaere, Charlotte (2021), *Soil Physicochemical Properties in rice fields from six sites in western Burkina Faso, 2018*. DataSuds, V1. [Dataset](https://doi.org/10.23708/LZ8A5B) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), stated in the dataset's Terms of Use |
| `irridrone_metadata.csv`, `irridrone_README.txt` | Afshar, Somayeh; Ramezani, Mohsen; Mollazade, Kaveh; Moradiani, Atefe (2026), *IrriDrone*. Zenodo, version 1.0. [Dataset](https://doi.org/10.5281/zenodo.22017449) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), stated in its README and API metadata |

Filenames were shortened locally; source bytes are unchanged. The audit parses numeric
commas in the Burkina Faso table and trims header whitespace in memory. It decodes the
IrriDrone CSV as Windows-1252; that decoding is an explicit audit choice. It does not
interpret the source dates, infer a timezone, rewrite measurements, execute author code
or download video archives. Derived summaries are Agronaut's work, not author endorsement.

The Taiwan candidate in the registry has no retained numeric files: metadata was public,
but attempted archive downloads returned HTTP 401 through the API and HTTP 403 through the
web tool. No authentication was supplied or access restriction bypassed.

Run `python -S -m scripts.audit_field_datasets` from the repository checkout to verify all
five files and reproduce the screening. These research files are not bundled into the
application wheel. See [the screening report](../../docs/dpg/field_evidence/README.md).
