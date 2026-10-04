"""The checks each fact type of the inspection makes of its own values
(docs/conventions.md, "Inspection"): each value a type refuses, and the error
it raises. The values are built here, not read from a file.
"""

from __future__ import annotations

import dataclasses

import pytest

from pdfacts import (
    Action,
    ActionPlace,
    ActionTrigger,
    ActiveContent,
    Contents,
    ContentsForm,
    ContentsStream,
    CryptFilter,
    CryptMethod,
    EmbeddedFile,
    Encryption,
    Inspection,
    Name,
    NameEncoding,
    ObjectCounts,
    ObjectRef,
    PageContents,
    Permissions,
    Unknown,
    UnknownReason,
)


def ref(number: int, generation: int = 0) -> ObjectRef:
    return ObjectRef(object_number=number, generation=generation)


def name(text: str) -> Name:
    return Name(raw=text.encode(), text=text, encoding=NameEncoding.UTF8)


def counts(objects: int, streams: int, dictionaries: int, arrays: int = 0) -> ObjectCounts:
    return ObjectCounts(
        total=objects,
        streams=streams,
        object_streams=0,
        cross_reference_streams=0,
        dictionaries=dictionaries,
        arrays=arrays,
    )


def contents(
    index: int, number: int, form: ContentsForm, named: int = 0, array=None
) -> PageContents:
    """The page at `index`, object `number`, its /Contents at `named` of Inspection.contents."""
    return PageContents(page_index=index, ref=ref(number), form=form, array=array, contents=named)


def named(*streams: ContentsStream, others: int = 0) -> Contents:
    return Contents(streams=streams, others=others)


NOTHING = ActiveContent(
    actions=(), sequences=(), triggers=(), xfa=None, embedded_files=(), embedded_streams=()
)


ALLOWED = Permissions(
    print=True,
    modify=True,
    copy=True,
    annotate=True,
    fill_forms=True,
    accessibility=True,
    assemble=True,
    print_high_quality=True,
)


def encryption(**changed) -> Encryption:
    values = {
        "ref": None,
        "filter": name("Standard"),
        "sub_filter": None,
        "v": 4,
        "r": 4,
        "length": 128,
        "p": -1,
        "permissions": ALLOWED,
        "encrypt_metadata": None,
        "stream_filter": None,
        "string_filter": None,
        "embedded_file_filter": None,
        "crypt_filters": (),
        "key_bits": 128,
        "stream_method": CryptMethod.IDENTITY,
        "string_method": CryptMethod.IDENTITY,
        "embedded_file_method": CryptMethod.IDENTITY,
        "user_password_matched": True,
        "owner_password_matched": False,
    }
    return Encryption(**(values | changed))


# An event each place has.
EVENT = {
    ActionPlace.DOCUMENT: "OpenAction",
    ActionPlace.DOCUMENT_JAVASCRIPT: None,
    ActionPlace.PAGE: "O",
    ActionPlace.ANNOTATION: "A",
    ActionPlace.FIELD: "K",
    ActionPlace.OUTLINE_ITEM: "A",
}


def trigger(place, page_index=None, annotation_index=None, event=...) -> ActionTrigger:
    return ActionTrigger(
        place=place,
        owner=ref(1),
        page_index=page_index,
        annotation_index=annotation_index,
        event=EVENT.get(place) if event is ... else event,
        action=0,
    )


def inspection(**changed) -> Inspection:
    values = {
        "size": 1,
        "objects": counts(0, 0, 0),
        "pages": (),
        "contents": (),
        "encryption": None,
        "active_content": NOTHING,
        "warnings": (),
    }
    return Inspection(**(values | changed))


def active(**changed) -> ActiveContent:
    values = {
        "actions": (),
        "sequences": (),
        "triggers": (),
        "xfa": None,
        "embedded_files": (),
        "embedded_streams": (),
    }
    return ActiveContent(**(values | changed))


def stream_of(number: int) -> ContentsStream:
    return ContentsStream(ref=ref(number), length=1, length_indirect=False)


CHECKS = {
    "an object number 0": (lambda: ObjectRef(object_number=0, generation=0), ValueError),
    "a generation below 0": (lambda: ref(1, -1), ValueError),
    "a float for a generation": (lambda: ObjectRef(object_number=1, generation=1.0), TypeError),
    "a generation above 65535": (lambda: ref(1, 65536), ValueError),
    "a bool for an object number": (
        lambda: ObjectRef(object_number=True, generation=0),
        TypeError,
    ),
    "a length below 0": (
        lambda: ContentsStream(ref=ref(1), length=-1, length_indirect=False),
        ValueError,
    ),
    "an int for a bool": (
        lambda: ContentsStream(ref=ref(1), length=1, length_indirect=1),
        TypeError,
    ),
    "a stream's ref no ObjectRef": (
        lambda: ContentsStream(ref=(1, 0), length=1, length_indirect=False),
        TypeError,
    ),
    "an array object for a stream": (
        lambda: contents(0, 3, ContentsForm.STREAM, stream_of(4), array=ref(6)),
        ValueError,
    ),
    "no array object for an indirect array": (
        lambda: contents(0, 3, ContentsForm.INDIRECT_ARRAY),
        ValueError,
    ),
    "a stream form without its stream": (
        lambda: inspection(pages=(contents(0, 3, ContentsForm.STREAM),), contents=(named(),)),
        ValueError,
    ),
    "another form with a stream": (
        lambda: inspection(
            pages=(contents(0, 3, ContentsForm.OTHER),), contents=(named(stream_of(4), others=1),)
        ),
        ValueError,
    ),
    "an absent form with others": (
        lambda: inspection(
            pages=(contents(0, 3, ContentsForm.ABSENT),), contents=(named(others=1),)
        ),
        ValueError,
    ),
    "a contents index below 0": (lambda: contents(0, 3, ContentsForm.ABSENT, -1), ValueError),
    "a page naming contents beyond them": (
        lambda: inspection(pages=(contents(0, 3, ContentsForm.ABSENT, 1),), contents=(named(),)),
        ValueError,
    ),
    "contents no page names": (
        lambda: inspection(
            pages=(contents(0, 3, ContentsForm.ABSENT),), contents=(named(), named())
        ),
        ValueError,
    ),
    "contents unknown, pages not": (
        lambda: inspection(contents=Unknown(UnknownReason.UNREADABLE)),
        ValueError,
    ),
    "contents of another kind": (lambda: inspection(contents=(1,)), TypeError),
    "streams of no ContentsStream": (lambda: Contents(streams=(1,), others=0), TypeError),
    "others below 0": (lambda: Contents(streams=(), others=-1), ValueError),
    "a page index below 0": (lambda: contents(-1, 3, ContentsForm.ABSENT), ValueError),
    "an array object no ObjectRef": (
        lambda: contents(0, 3, ContentsForm.INDIRECT_ARRAY, array=(6, 0)),
        TypeError,
    ),
    "a form of no ContentsForm": (
        lambda: PageContents(page_index=0, ref=ref(3), form="banana", array=None, contents=0),
        ValueError,
    ),
    "a page's ref no ObjectRef": (
        lambda: PageContents(page_index=0, ref=3, form=ContentsForm.ABSENT, array=None, contents=0),
        TypeError,
    ),
    "a count below 0": (lambda: counts(0, -1, 0), ValueError),
    "dictionaries below 0": (lambda: counts(0, 0, -1), ValueError),
    "arrays below 0": (lambda: counts(0, 0, 0, -1), ValueError),
    "more of the kinds than the total": (lambda: counts(2, 1, 1, 1), ValueError),
    "bits 9 to 12 in part": (lambda: dataclasses.replace(ALLOWED, accessibility=None), ValueError),
    "a crypt filter's name no Name": (lambda: CryptFilter(name=b"StdCF", cfm=None), TypeError),
    "a crypt filter's /CFM no Name": (
        lambda: CryptFilter(name=name("StdCF"), cfm=b"V2"),
        TypeError,
    ),
    "an encryption's ref no ObjectRef": (lambda: encryption(ref=(5, 0)), TypeError),
    "a filter no Name": (lambda: encryption(filter=b"Standard"), TypeError),
    "a stream filter no Name": (lambda: encryption(stream_filter="StdCF"), TypeError),
    "a string filter no Name": (lambda: encryption(string_filter="StdCF"), TypeError),
    "a sub-filter no Name": (lambda: encryption(sub_filter="x"), TypeError),
    "an embedded file filter no Name": (lambda: encryption(embedded_file_filter="x"), TypeError),
    "a /V no int": (lambda: encryption(v=4.0), TypeError),
    "a bool for /R": (lambda: encryption(r=True), TypeError),
    "a bool for /Length": (lambda: encryption(length=True), TypeError),
    "a /Length no int": (lambda: encryption(length="128"), TypeError),
    "permissions not those of /P": (lambda: encryption(p=-5), ValueError),
    "/EncryptMetadata no bool": (lambda: encryption(encrypt_metadata=1), TypeError),
    "crypt filters no CryptFilters": (
        lambda: encryption(crypt_filters=(name("StdCF"),)),
        TypeError,
    ),
    "a key of 0 bits": (lambda: encryption(key_bits=0), ValueError),
    "a key of bits below 0": (lambda: encryption(key_bits=-7), ValueError),
    "a method of no CryptMethod": (lambda: encryption(stream_method="des"), ValueError),
    "a password matched no bool": (lambda: encryption(user_password_matched=1), TypeError),
    "a /V of 3": (lambda: encryption(v=3), ValueError),
    "AES for embedded files at /V 2": (
        lambda: encryption(
            v=2,
            r=3,
            stream_method=CryptMethod.RC4,
            string_method=CryptMethod.RC4,
            embedded_file_method=CryptMethod.AES,
        ),
        ValueError,
    ),
    "AES for /V 2": (
        lambda: encryption(v=2, r=3, stream_method=CryptMethod.AES),
        ValueError,
    ),
    "bits 3 to 6 no bools": (lambda: dataclasses.replace(ALLOWED, copy=1), TypeError),
    "bits 9 to 12 no bools": (lambda: dataclasses.replace(ALLOWED, fill_forms=1), TypeError),
    "bytes for a kind": (lambda: Action(kind=b"URI", ref=ref(1), next=None), TypeError),
    "an action's ref no ObjectRef": (lambda: Action(kind=None, ref=1, next=None), TypeError),
    "a next below 0": (lambda: Action(kind=None, ref=ref(1), next=-1), ValueError),
    "a bool for a next": (lambda: Action(kind=None, ref=ref(1), next=True), TypeError),
    "a str for a next": (lambda: Action(kind=None, ref=ref(1), next="0"), TypeError),
    "an owner no ObjectRef": (
        lambda: ActionTrigger(
            place=ActionPlace.DOCUMENT,
            owner=(1, 0),
            page_index=None,
            annotation_index=None,
            event="OpenAction",
            action=0,
        ),
        TypeError,
    ),
    "no action for a trigger": (
        lambda: ActionTrigger(
            place=ActionPlace.DOCUMENT,
            owner=ref(1),
            page_index=None,
            annotation_index=None,
            event="OpenAction",
            action=None,
        ),
        TypeError,
    ),
    "a page's entry with an annotation index": (
        lambda: trigger(ActionPlace.PAGE, 0, 0, "O"),
        ValueError,
    ),
    "bytes for an event": (lambda: trigger(ActionPlace.DOCUMENT, event=b"A"), TypeError),
    "an embedded file's streams below 0": (
        lambda: EmbeddedFile(specification=ref(9), streams=-1),
        ValueError,
    ),
    "a list for the actions": (lambda: active(actions=[]), TypeError),
    "a list for the triggers": (lambda: active(triggers=[]), TypeError),
    "a string method of no CryptMethod": (lambda: encryption(string_method="des"), ValueError),
    "an embedded file method of no CryptMethod": (
        lambda: encryption(embedded_file_method="des"),
        ValueError,
    ),
    "two direct arrays under one index": (
        lambda: inspection(
            pages=(contents(0, 3, ContentsForm.ARRAY), contents(1, 5, ContentsForm.ARRAY)),
            contents=(named(stream_of(4)), named(stream_of(4))),
        ),
        ValueError,
    ),
    "one indirect array under two indices": (
        lambda: inspection(
            pages=(
                contents(0, 3, ContentsForm.INDIRECT_ARRAY, 0, ref(6)),
                contents(1, 5, ContentsForm.INDIRECT_ARRAY, 1, ref(7)),
                contents(2, 8, ContentsForm.INDIRECT_ARRAY, 1, ref(6)),
            ),
            contents=(named(stream_of(4)), named(stream_of(4))),
        ),
        ValueError,
    ),
    "two indirect arrays under one index": (
        lambda: inspection(
            pages=(
                contents(0, 3, ContentsForm.INDIRECT_ARRAY, 0, ref(6)),
                contents(1, 5, ContentsForm.INDIRECT_ARRAY, 0, ref(7)),
            ),
            contents=(named(stream_of(4)), named(stream_of(4))),
        ),
        ValueError,
    ),
    "a stream and an indirect array under one index": (
        lambda: inspection(
            pages=(
                contents(0, 3, ContentsForm.STREAM, 0),
                contents(1, 5, ContentsForm.INDIRECT_ARRAY, 0, ref(6)),
            ),
            contents=(named(stream_of(4)), named(stream_of(5))),
        ),
        ValueError,
    ),
    "contents out of the order the pages name them": (
        lambda: inspection(
            pages=(contents(0, 3, ContentsForm.ARRAY, 1), contents(1, 5, ContentsForm.ARRAY, 0)),
            contents=(named(stream_of(4)), named(stream_of(4))),
        ),
        ValueError,
    ),
    "a list for the embedded files": (lambda: active(embedded_files=[]), TypeError),
    "a list for the embedded streams": (lambda: active(embedded_streams=[]), TypeError),
    "an annotation index below 0": (
        lambda: trigger(ActionPlace.ANNOTATION, 0, -1),
        ValueError,
    ),
    "a field's entry with a page and no annotation index": (
        lambda: trigger(ActionPlace.FIELD, 0, None, "K"),
        ValueError,
    ),
    "an entry's page index below 0": (
        lambda: trigger(ActionPlace.ANNOTATION, -1, 0),
        ValueError,
    ),
    "XFA streams no ObjectRefs": (lambda: active(xfa=((11, 0),)), TypeError),
    "embedded streams no ObjectRefs": (
        lambda: active(
            embedded_files=(EmbeddedFile(specification=ref(9), streams=0),),
            embedded_streams=(((10, 0),),),
        ),
        TypeError,
    ),
    "an annotation index without a page": (
        lambda: trigger(ActionPlace.ANNOTATION, None, 0),
        ValueError,
    ),
    "a page's entry without a page": (lambda: trigger(ActionPlace.PAGE, event="O"), ValueError),
    "the document's entry with a page": (lambda: trigger(ActionPlace.DOCUMENT, 0, 0), ValueError),
    "a place of no ActionPlace": (lambda: trigger("catalog"), ValueError),
    "an empty event": (lambda: trigger(ActionPlace.DOCUMENT, event=""), TypeError),
    "an event of another place": (lambda: trigger(ActionPlace.PAGE, 0, event="A"), ValueError),
    "an annotation's event for a field": (
        lambda: trigger(ActionPlace.FIELD, event="E"),
        ValueError,
    ),
    "an entry of the name tree with an event": (
        lambda: trigger(ActionPlace.DOCUMENT_JAVASCRIPT, event="A"),
        ValueError,
    ),
    "another entry without an event": (
        lambda: trigger(ActionPlace.OUTLINE_ITEM, event=None),
        ValueError,
    ),
    "an embedded file's specification no ObjectRef": (
        lambda: EmbeddedFile(specification=9, streams=0),
        TypeError,
    ),
    "a next beyond the sequences": (
        lambda: active(actions=(Action(kind=None, ref=ref(1), next=0),)),
        ValueError,
    ),
    "a sequence beyond the actions": (lambda: active(sequences=((0,),)), ValueError),
    "a bool for a sequence's index": (
        lambda: active(
            actions=(Action(kind=None, ref=ref(1), next=None),) * 2, sequences=((True,),)
        ),
        TypeError,
    ),
    "a sequence's index below 0": (
        lambda: active(actions=(Action(kind=None, ref=ref(1), next=None),), sequences=((-1,),)),
        ValueError,
    ),
    "a sequence no tuple": (
        lambda: active(actions=(Action(kind=None, ref=ref(1), next=None),), sequences=([0],)),
        TypeError,
    ),
    "an entry of the name tree with a page": (
        lambda: trigger(ActionPlace.DOCUMENT_JAVASCRIPT, 0, None, None),
        ValueError,
    ),
    "an outline item's entry with a page": (
        lambda: trigger(ActionPlace.OUTLINE_ITEM, 0, 0),
        ValueError,
    ),
    "a trigger beyond the actions": (
        lambda: active(triggers=(trigger(ActionPlace.DOCUMENT),)),
        ValueError,
    ),
    "an embedded file beyond its streams": (
        lambda: active(embedded_files=(EmbeddedFile(specification=ref(1), streams=0),)),
        ValueError,
    ),
    "actions unknown, sequences not": (
        lambda: active(actions=Unknown(UnknownReason.UNREADABLE)),
        ValueError,
    ),
    "a size of 0": (lambda: inspection(size=0), ValueError),
    "a size no int": (lambda: inspection(size="5"), TypeError),
    "a bool for a size": (lambda: inspection(size=True), TypeError),
    "no counts": (lambda: inspection(objects=None), TypeError),
    "pages no tuple": (lambda: inspection(pages=[]), TypeError),
    "a page at another index": (
        lambda: inspection(pages=(contents(1, 3, ContentsForm.ABSENT),), contents=(named(),)),
        ValueError,
    ),
    "an encryption no Encryption": (lambda: inspection(encryption=True), TypeError),
    "no active content": (lambda: inspection(active_content=None), TypeError),
    "warnings no str": (lambda: inspection(warnings=(b"w",)), TypeError),
}


@pytest.mark.parametrize("check", CHECKS.values(), ids=CHECKS.keys())
def test_each_value_is_checked(check):
    make, error = check
    with pytest.raises(error):
        make()
