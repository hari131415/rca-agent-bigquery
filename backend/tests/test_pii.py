"""Tests for PII scrubbing of complaint text."""

from data.loaders import pii


def test_full_vin_redacted() -> None:
    result = pii.scrub_text("MY VIN IS 1HGCM82633A004352 AND IT FAILED.")
    assert result.text == "MY VIN IS [VIN] AND IT FAILED."
    assert result.vin_redactions == 1
    assert result.redacted


def test_partial_vin_after_vin_word() -> None:
    result = pii.scrub_text("VIN # NH106286, PLEASE CHECK.")
    assert result.text == "VIN # [VIN], PLEASE CHECK."
    assert result.vin_redactions == 1


def test_partial_vin_after_vin_number_is() -> None:
    result = pii.scrub_text("VIN NUMBER IS JM1FD33 FOR THIS CAR.")
    assert result.text == "VIN NUMBER IS [VIN] FOR THIS CAR."
    assert result.vin_redactions == 1


def test_ending_in_partial_vin() -> None:
    result = pii.scrub_text("THE PROBLEM CAR ENDING IN ZF49715.")
    assert result.text == "THE PROBLEM CAR ENDING IN [VIN]."
    assert result.vin_redactions == 1


def test_vin_number_phrase_not_redacted() -> None:
    # "NUMBER" has no digit, so it is not a VIN token.
    result = pii.scrub_text("PLEASE PROVIDE THE VIN NUMBER.")
    assert result.text == "PLEASE PROVIDE THE VIN NUMBER."
    assert result.vin_redactions == 0
    assert not result.redacted


def test_bare_token_without_vin_context_not_redacted() -> None:
    result = pii.scrub_text("PART NUMBER ABC1234 FAILED TWICE.")
    assert result.text == "PART NUMBER ABC1234 FAILED TWICE."
    assert result.vin_redactions == 0


def test_four_wheel_drive_not_an_address() -> None:
    result = pii.scrub_text("WHEN IN 4 WHEEL DRIVE, BRAKES LOCK UP.")
    assert result.text == "WHEN IN 4 WHEEL DRIVE, BRAKES LOCK UP."
    assert result.address_redactions == 0


def test_full_form_address_redacted() -> None:
    result = pii.scrub_text("I TOOK IT TO 529 VIRGINIA BEACH BLVD., VIRGINIA BEACH, VA 23451.")
    assert result.text == "I TOOK IT TO [ADDRESS]."
    assert result.address_redactions == 1


def test_address_without_zip_not_caught() -> None:
    # Documented limit: no state+ZIP, no redaction.
    result = pii.scrub_text("I LIVE AT 123 MAIN ST, SPRINGFIELD.")
    assert result.address_redactions == 0


def test_phone_and_email_detected() -> None:
    assert pii.contains_contact_info("CALL ME AT 555-123-4567.")
    assert pii.contains_contact_info("CALL (555) 123-4567.")
    assert pii.contains_contact_info("REACH ME AT OWNER@EXAMPLE.COM.")
    assert not pii.contains_contact_info("BRAKES FAILED AT 30000 MILES.")


def test_scrub_counts_both_kinds() -> None:
    result = pii.scrub_text(
        "VIN 1HGCM82633A004352. DEALER AT 6519 UNIVERSITY DR. HUNTSVILLE, AL 35806."
    )
    assert result.vin_redactions == 1
    assert result.address_redactions == 1
    assert result.text == "VIN [VIN]. DEALER AT [ADDRESS]."
