# Form filling, later, by adding modules only

A design note (issue #15): how form filling can be added to pdfacts after
1.0 without changing what exists, measured against the seven rules of
`docs/architecture.md`. The note is written in M5b, from the interface as
it is then. It makes no change and promises nothing.

**1. Which new packages and modules?** A feature package `forms/edit/`
(rule 6: one package per feature; reading lives in `forms/`, writing gets
its own), holding the operation and the appearance builder; a module
`backends/fonts/edit.py` behind it for the fonts a builder may embed (rule
5: backends are split by capability; core never imports either).

**2. Which existing public names, and which would change?** It uses
`open()`, `Document.pages`, `fields()` and `widgets()` for the reading
side; `Field`, `FieldRef`, `Widget` and `Widget.ref` to point at the
field; `EditSession.add()`, `apply()`, `Edited` and `Limits` for the
writing side; `Rect` and `DeviceColour` for the appearance geometry;
`pdfacts.core.values.Unknown` for what a writer could not honour. **The
list of names that would have to change is empty** — the skeleton holds:
every need above is an existing name used as it is.

**3. Pointing at the field.** A caller reads `fields()`, matches on the
qualified name or the export value, and holds the `Field`'s `ref` (M2's
`FieldRef`: page, index and object number — rule 1, everything
addressable). One field may have several widgets; the caller picks them
from `widgets()` by `Widget.field`'s equality with the field's `ref`.

**4. The operation and the session.** A `Fill(field_ref, value)` operation
in `forms/edit/` (rule 3: a change is expressed as an operation; facts are
immutable), collected by `EditSession.add()` beside `Rewrite` and
`Decrypt`, applied with them in one step. Setting a value is per widget
what `/V` accepts (M2's readers already classify it); the appearance the
value needs is built into a new stream the operation names.

**5. Verifying the result.** Rule 4: after applying, the session reopens
the result. `Fill`'s check is the forms reader's own — `fields()` and
`widgets()` on the written file must report the value set and the
appearance present — beside the session's standing checks (the result opens,
its pages are as many as QPDF wrote, no warning).

**6. New backend protocol, and the engine.** A writer protocol beside the
reader ones (rule 5), asking for a field's value to be set, an appearance
stream written and a font embedded. PDFium could implement the first two —
its form interface is the natural source — but each claim is unproven
(question 7); QPDF, which rewrites the whole file (`Saving`), can write
the stream and embed the font where the building is pdfacts' own.

**7. What has to be examined first.** Each unknown below is an experiment
with PDFium or QPDF, recorded in `docs/capabilities.md` before anything is
promised: whether PDFium sets a field value through its form interface and
builds an appearance stream when it does (unknown); whether either engine
embeds a font (unknown); whether PDFium's incremental save keeps an
existing signature valid (unknown; `Saving` names the constraint); what
`/V` accepts for every field type beyond M2's reading of it (unknown);
which writers mangle the appearance of a flattened field (unknown).

The note stays after 1.0: it moves nothing into the roadmap.
