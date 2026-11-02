# TekniGO

Original, unmodified transparent PNGs from the supplied attachments:

- `logo-original.png`: `TG_Mesa de trabajo 1.png`, SHA256 `a97733ad58b8c46632b7b0785e46913f16746288399e45504e17ad3f0c023cf3`.
- `isotipo-original.png`: `TG-02.png`, SHA256 `8878fe4110becc4d61428831d5df60c2c44f4d80e83d2c7ef149a5fcac61a29b`.

Use `components/Logo.tsx` to display these assets. It removes only transparent
margins through the SVG viewBox and applies an sRGB filter, retaining source
geometry and alpha. Blue source regions become `#2563EB`; orange regions become
`#EA580C`, matching `--blue` and `--orange` in `styles.css`. The hover blue is
`#1D4ED8`. If the palette changes, update the RGB arrays in `Logo.tsx` as well.

`Brand` uses the full logo in shared access/registration headers.
`LoadingState` uses the symbol for screen, section and inline variants, including
account, profile, coverage, specialties, services and identity verification.
Its status text, slow-response message and reduced-motion support are retained.

Validation: frontend build and all 12 tests pass; filter matrix outputs checked
against CSS HEX values with the alpha row unchanged; originals checked by SHA256;
desktop (1265 px) and mobile (375 px) registration and loading visually reviewed.
Loading was reviewed with a temporary local fixture without signing in or
uploading identity documents. The fixture was removed after review.
