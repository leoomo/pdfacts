# Architecture

Status: design. Only `core/engines.py` and the two backend modules exist.

## Layers

```text
callers
   |
public interface      open / Page / facts / forms / inspect / edit session
   |
feature packages      text, graphics, annotations, forms, inspect, edit, render
core                  geometry, object references, errors, limits,
                      capabilities, runtime, digest
   |
backend protocols     one protocol per capability
   |
backend modules       pdfium (what needs interpretation)
                      qpdf   (file structure)
```

Dependencies point downwards. Core never imports a feature package.

## The two engines

| Engine | Responsible for |
| --- | --- |
| PDFium | what needs interpretation: text positions, paths, rendering |
| QPDF | file structure: objects, dictionaries, streams, rewrite, decryption |

A value that is written directly in a dictionary may be read through QPDF:
object numbers, `UserUnit`, the field tree. Whether pdfacts interprets content
streams itself is not decided; the text probe in
[ROADMAP.md](../ROADMAP.md) answers that.

Every document is opened by both engines. They are checked against each other
at run time, and a value that depends on a failed check is unknown. The checks
are listed under "Object identity" in [conventions.md](conventions.md).

## Package layout

```text
src/pdfacts/
  core/          document, geometry, refs, errors, limits,
                 capabilities, runtime, digest, engines
  backends/      protocols, pdfium, qpdf
  text/          glyphs, layout
  graphics/      paths, images
  annotations/   model, reader            later: ops, appearance
  forms/         model, reader            later: fill
  inspect/       report, active_content
  edit/          session, operations
  render/
  content/       content-stream tokens and graphics state
```

## Rules that keep new features additive

These seven rules are fixed now so that adding a feature later means adding a
package, not changing existing interfaces.

### 1. Everything is addressable

Every fact that corresponds to a PDF object carries a reference: page index,
kind, index within the page and, when it can be obtained, the object number.
Changing one particular annotation later requires pointing at it exactly.
Adding references afterwards would change every fact type.

### 2. Annotation is the base type

`Annotation` holds what all annotations share. `Widget` is a subtype that links
to a form field. Comments, links and form widgets are the same kind of object
in the file.

### 3. Reading and writing are separate

Facts are immutable. A change is expressed as an operation. An edit session
collects operations and applies them in one step. Write features can therefore
be added one at a time without touching the read model.

### 4. Writes are verified

After applying, the session reopens the result and checks every operation
against its expected outcome. A write that went wrong is found immediately.

### 5. Backends are split by capability

Feature packages depend on protocols such as text reader, annotation reader or
writer, never on an engine. Replacing or adding an engine does not touch
feature code.

### 6. One package per feature

Core does not import features. Adding annotations does not touch text or forms.

### 7. Structures only grow

Fact structures carry a schema version. A minor version may add optional
fields. Removing or changing a field needs a major version.

This rule takes effect with version 1.0. Before that, the interface may change
in ways that break callers, and every such change is recorded in
[CHANGELOG.md](../CHANGELOG.md).

## How much of this exists at each milestone

A milestone builds only the parts of the skeleton that its own features call.

| Part | Arrives with | First caller |
| --- | --- | --- |
| runtime, page references, protocols for geometry, rendering and object access | M1 | open, page geometry, rendering |
| annotation references | M2 | annotation reader |
| writer protocol, edit session | M5 | rewrite and decrypt |

Form filling is the feature the skeleton is measured against. It needs
appearance generation, font embedding and, for signed files, incremental
saving, which makes it the hardest of the planned write features. M1 ends with
a one-page note that shows how it can be added by adding modules only.

## Saving

A write backend declares whether it supports incremental saving. QPDF rewrites
the whole file. PDFium can save incrementally. A signed file keeps its
signature only with an incremental save, so callers must be able to ask.

## Planned features and the rules they rely on

| Feature | Reading | Writing | Relies on |
| --- | --- | --- | --- |
| Annotations | common properties in M2, subtype details later | add, change, remove, build appearance | references, edit session, writer protocol |
| Form filling and flattening | M2 | set value, build appearance, embed fonts | same |
| Merge, split, rotate pages | M1 | page operations | edit session |
| Outlines, links, attachments, metadata | new reader packages | new operations | object backend |
| Redaction, watermarks | content-stream interpretation | content-stream rewriting | `content/` |
| Signatures | read signature fields | no signing; incremental save keeps existing signatures | incremental save |

## Process-wide constraints

PDFium is initialized once per process, its font configuration is process-wide,
and it must not be called from several threads at once. The runtime therefore
allows one font policy per process and serializes calls. Callers that need
isolation between documents should use separate processes.

## Enforcement

`tools/check_architecture.py` checks three rules on every test run:

| Rule | Meaning |
| --- | --- |
| engine-boundary | pypdfium2 only in `backends/pdfium.py`, pikepdf only in `backends/qpdf.py` |
| forbidden | MuPDF bindings are never imported by the library |
| core-isolation | `pdfacts.core` does not import feature packages |

The table states what the tool checks today. M1 extends the `forbidden` rule
to `tests/` and `tools/`.

An abstraction is added only when a feature uses it. The rules above do not
justify code that nothing calls.
