"""One paragraph = one text message.

The protocol used to split only on an explicit `---`, so a three-paragraph reply arrived
as a single block with blank lines inside -- something nobody types on Messenger. The
reference case is reused here verbatim as a test sample.

The model is buffered until the terminal result, then `textutils` produces the bubbles
sent by the transport.
"""

from test_claude_stream import assistant, consume, text

from redline.service.textutils import BURST_MAX, burst_parts

THREE_PARAGRAPHS = """D'après ce que tu décris, c'est pas juste "plus douloureux", \
c'est un changement de nature du signal.

Ce soir tu ne fais pas le renfo A, même en isométrique. Donne-moi un chiffre sur 10 \
pour cette douleur passive.

Ton RDV du 27/08 est déjà pris. Si le chiffre ne redescend pas, tu l'avances."""


def _wire_then_burst(message: str) -> list:
    return burst_parts(consume([assistant(text(message))]).full)


def test_a_paragraph_is_a_bubble():
    parts = burst_parts(THREE_PARAGRAPHS)
    assert len(parts) == 3
    assert parts[0].startswith("D'après ce que tu décris")
    assert parts[1].startswith("Ce soir tu ne fais pas")
    assert parts[2].startswith("Ton RDV du 27/08")
    assert not any("\n\n" in p for p in parts)


def test_the_wire_path_cuts_identically():
    assert _wire_then_burst(THREE_PARAGRAPHS) == burst_parts(THREE_PARAGRAPHS)


def test_a_paragraph_arriving_in_pieces_is_not_cut_early():
    """The lookahead earns its keep here: the model writes « ... signal.\\n\\n- » and
    the next token has not landed. Deciding then would break a list into bubbles."""
    lines = [assistant(text("Ce qu'il me faut :\n\n")),
             assistant(text("- le RPE\n- la douleur"))]
    parts = burst_parts(consume(lines).full)
    assert len(parts) == 1
    assert "- le RPE" in parts[0]


def test_two_assistant_messages_are_two_bubbles():
    """Consecutive assistant messages are already joined by a blank line upstream,
    so the paragraph rule applies to them just the same."""
    lines = [assistant(text("Premier paragraphe.")),
             assistant(text("Second paragraphe."))]
    parts = burst_parts(consume(lines).full)
    assert parts == ["Premier paragraphe.", "Second paragraphe."]


def test_a_list_stays_with_the_line_that_introduces_it():
    """« : » announces what follows. Cutting there would strand the colon at the end
    of a text and deliver the list as an orphan — and a five-item list as five texts."""
    message = ("Trois choses après la séance :\n\n"
               "- le RPE\n- le pic de douleur\n- la cheville\n\n"
               "Et si tu as coupé une série, dis-le.")
    parts = burst_parts(message)
    assert len(parts) == 2
    assert parts[0].endswith("la cheville")
    assert "- le RPE" in parts[0]
    assert parts[1] == "Et si tu as coupé une série, dis-le."


def test_a_numbered_list_holds_together_too():
    message = "Le plan tient en deux temps :\n\n1. ce soir, la séance A\n2. demain, la lecture"
    assert len(burst_parts(message)) == 1


def test_a_list_split_by_blank_lines_is_still_one_bubble():
    """Items breathing on their own lines are still one thought, not four texts."""
    message = "Ce qu'il me faut :\n\n- le RPE\n\n- la douleur\n\n- la cheville"
    assert len(burst_parts(message)) == 1


def test_the_explicit_separator_still_cuts():
    """`---` survives as the way to force a cut where no blank line exists."""
    assert burst_parts("Premier\n---\nDeuxième") == ["Premier", "Deuxième"]


def test_paragraphs_are_capped_like_blocks():
    message = "\n\n".join(f"Paragraphe numéro {i}." for i in range(1, 8))
    parts = burst_parts(message)
    assert len(parts) == BURST_MAX
    for i in range(1, 8):
        assert f"Paragraphe numéro {i}." in "\n\n".join(parts)


def test_the_wire_path_caps_without_losing_a_paragraph():
    """The cap rejoins the tail; it never drops it. A burst too long is a prompt
    problem, never a reason to lose a sentence."""
    message = "\n\n".join(f"Paragraphe numéro {i}." for i in range(1, 8))
    parts = _wire_then_burst(message)
    assert len(parts) == BURST_MAX
    for i in range(1, 8):
        assert f"Paragraphe numéro {i}." in "\n\n".join(parts)


def test_a_single_paragraph_stays_a_single_text():
    assert burst_parts("Juste une phrase.") == ["Juste une phrase."]
