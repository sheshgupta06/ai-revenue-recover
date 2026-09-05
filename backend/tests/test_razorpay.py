import pytest
from unittest.mock import MagicMock, patch
import razorpay.errors as errors
from app.core.config import settings
from app.services.razorpay_service import (
    RazorpayService,
    RazorpayConfigError,
    RazorpayAPIError,
    RazorpayPaymentDetails,
    RazorpayPaymentLinkDetails,
)

def test_razorpay_service_missing_credentials() -> None:
    """
    Verifies that RazorpayService raises a RazorpayConfigError if credentials are not configured.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", None), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", None):
        
        service = RazorpayService()
        with pytest.raises(RazorpayConfigError) as exc_info:
            _ = service.client
            
        assert "not configured" in str(exc_info.value)

def test_razorpay_service_production_placeholder_credentials() -> None:
    """
    Verifies that RazorpayService raises a RazorpayConfigError in production if placeholder keys are used.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", "rzp_test_placeholder_key_id"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "rzp_test_placeholder_key_secret"), \
         patch.object(settings, "ENV", "production"):
        
        service = RazorpayService()
        with pytest.raises(RazorpayConfigError) as exc_info:
            _ = service.client
            
        assert "placeholder Razorpay credentials" in str(exc_info.value)

@patch("app.services.razorpay_service.razorpay.Client")
def test_get_payment_details_success(mock_client_class: MagicMock) -> None:
    """
    Verifies that get_payment_details successfully fetches and maps payment info when API behaves normally.
    """
    # Configure mock settings keys to avoid config exceptions
    with patch.object(settings, "RAZORPAY_KEY_ID", "test_key"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
        
        # Set up SDK mock response
        mock_payment = MagicMock()
        mock_payment.fetch.return_value = {
            "id": "pay_FN8r7nUvj123",
            "amount": 250000,
            "currency": "INR",
            "status": "captured",
            "method": "upi",
            "created_at": 1592913036,
            "error_description": None
        }
        
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.payment = mock_payment

        service = RazorpayService()
        result = service.get_payment_details("pay_FN8r7nUvj123")

        assert isinstance(result, RazorpayPaymentDetails)
        assert result.id == "pay_FN8r7nUvj123"
        assert result.amount == 250000
        assert result.currency == "INR"
        assert result.status == "captured"
        assert result.method == "upi"
        assert result.failure_reason is None
        assert result.created_at == 1592913036

        # Assert correct SDK method was called
        mock_payment.fetch.assert_called_once_with("pay_FN8r7nUvj123")

@patch("app.services.razorpay_service.razorpay.Client")
def test_get_payment_details_failed_payment(mock_client_class: MagicMock) -> None:
    """
    Verifies that get_payment_details maps failure reason correctly for unsuccessful payment transactions.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", "test_key"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
        
        mock_payment = MagicMock()
        mock_payment.fetch.return_value = {
            "id": "pay_FN8r7nUvj123",
            "amount": 10000,
            "currency": "INR",
            "status": "failed",
            "method": "card",
            "created_at": 1592913036,
            "error_description": "Card has expired"
        }
        
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.payment = mock_payment

        service = RazorpayService()
        result = service.get_payment_details("pay_FN8r7nUvj123")

        assert result.status == "failed"
        assert result.failure_reason == "Card has expired"

@patch("app.services.razorpay_service.razorpay.Client")
def test_get_payment_details_api_error(mock_client_class: MagicMock) -> None:
    """
    Verifies that get_payment_details handles SDK exceptions gracefully and raises custom RazorpayAPIError.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", "test_key"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
        
        mock_payment = MagicMock()
        # Simulate BadRequestError raising from SDK
        mock_payment.fetch.side_effect = errors.BadRequestError("Payment ID not found")
        
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.payment = mock_payment

        service = RazorpayService()
        with pytest.raises(RazorpayAPIError) as exc_info:
            service.get_payment_details("pay_invalid_id")

        assert "Razorpay API call failed" in str(exc_info.value)

@patch("app.services.razorpay_service.razorpay.Client")
def test_get_payment_details_unexpected_exception(mock_client_class: MagicMock) -> None:
    """
    Verifies that general exceptions like network timeouts are wrapped in RazorpayAPIError.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", "test_key"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
        
        mock_payment = MagicMock()
        # Simulate connection timeout exception
        mock_payment.fetch.side_effect = Exception("Connection timed out")
        
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.payment = mock_payment

        service = RazorpayService()
        with pytest.raises(RazorpayAPIError) as exc_info:
            service.get_payment_details("pay_FN8r7nUvj123")

        assert "parsing failure calling Razorpay API" in str(exc_info.value)

@patch("app.services.razorpay_service.razorpay.Client")
def test_create_payment_link_success(mock_client_class: MagicMock) -> None:
    """
    Verifies that create_payment_link calls the correct SDK endpoint and maps result successfully.
    """
    with patch.object(settings, "RAZORPAY_KEY_ID", "test_key"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
        
        mock_payment_link = MagicMock()
        mock_payment_link.create.return_value = {
            "id": "plink_FN8r7nUvj123",
            "short_url": "https://rzp.io/i/abcdef",
            "status": "created",
            "reference_id": "ref_123"
        }
        
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.payment_link = mock_payment_link

        service = RazorpayService()
        result = service.create_payment_link(
            amount=50000,
            description="Test payment link description",
            reference_id="ref_123"
        )

        assert isinstance(result, RazorpayPaymentLinkDetails)
        assert result.id == "plink_FN8r7nUvj123"
        assert result.short_url == "https://rzp.io/i/abcdef"
        assert result.status == "created"
        assert result.reference_id == "ref_123"

        # Assert correct parameters were sent
        mock_payment_link.create.assert_called_once_with(data={
            "amount": 50000,
            "currency": "INR",
            "accept_partial": False,
            "description": "Test payment link description",
            "reference_id": "ref_123"
        })
@patch("app.services.razorpay_service.razorpay.Client")
def test_create_payment_link_omits_repeating_synthetic_contact(mock_client_class: MagicMock) -> None:
    mock_client = mock_client_class.return_value
    mock_client.payment_link.create.return_value = {
        "id": "plink_test_contact",
        "short_url": "https://rzp.io/i/contact_test",
        "status": "created",
        "reference_id": "case_contact",
    }
    service = RazorpayService()

    service.create_payment_link(
        10000,
        "Contact validation",
        "case_contact",
        customer_email="customer@example.com",
        customer_phone="+91990000018",
    )

    payload = mock_client.payment_link.create.call_args.kwargs["data"]
    assert "contact" not in payload["customer"]

