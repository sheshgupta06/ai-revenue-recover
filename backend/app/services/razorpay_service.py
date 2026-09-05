import razorpay
import razorpay.errors as errors
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.logging import logger

class RazorpayConfigError(Exception):
    """Raised when Razorpay credentials are missing or misconfigured."""
    pass

class RazorpayAPIError(Exception):
    """Raised when an API call to Razorpay fails or times out."""
    pass

class RazorpayPaymentDetails(BaseModel):
    id: str = Field(description="The Razorpay payment unique identifier (e.g. pay_...)")
    amount: int = Field(description="The transaction amount in paisa (e.g., 50000 representing ₹500.00)")
    currency: str = Field("INR", description="Three-letter currency code")
    status: str = Field(description="Payment status: created, authorized, captured, refunded, failed")
    method: str = Field(description="Payment method used: card, netbanking, wallet, upi, etc.")
    failure_reason: str | None = Field(None, description="Detailed explanation of the failure description, if failed")
    created_at: int = Field(description="Epoch timestamp (seconds) when payment was created")

class RazorpayPaymentLinkDetails(BaseModel):
    id: str = Field(description="The unique payment link identifier (e.g. plink_...)")
    short_url: str = Field(description="The short URL which leads to the checkout page")
    status: str = Field(description="Status of the payment link: created, partially_paid, paid, expired, cancelled")
    reference_id: str | None = Field(None, description="Internal reference identifier mapping this link to our system")

class RazorpayService:
    def __init__(self) -> None:
        """
        Initializes the service by resolving credentials from settings.
        Validates keys lazily upon client property retrieval.
        """
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self._client = None

    @property
    def client(self) -> razorpay.Client:
        """
        Lazily instantiates and returns the official Razorpay Client.
        Fails clearly with RazorpayConfigError if credentials are not configured.
        """
        if not self.key_id or not self.key_secret:
            raise RazorpayConfigError(
                "Razorpay API credentials (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET) "
                "are not configured. Please add them to your environment variables or local .env file."
            )
        
        # Verify key placeholders aren't used in production-like environments
        if (self.key_id.startswith("rzp_test_placeholder") or self.key_secret.startswith("rzp_test_placeholder")) and settings.ENV == "production":
            raise RazorpayConfigError(
                "Invalid placeholder Razorpay credentials configured for production environment."
            )

        if self._client is None:
            try:
                self._client = razorpay.Client(auth=(self.key_id, self.key_secret))
                logger.info("razorpay_client_initialized", key_id_prefix=self.key_id[:8])
            except Exception as e:
                logger.error("razorpay_client_init_failed", error=str(e))
                raise RazorpayConfigError(f"Failed to initialize official Razorpay client: {e}")
        
        return self._client

    def get_payment_details(self, payment_id: str) -> RazorpayPaymentDetails:
        """
        Fetches payment details using the official SDK method.
        Wraps errors into custom typed models and logs events.
        """
        logger.info("razorpay_get_payment_details_started", payment_id=payment_id)
        
        try:
            # Call official SDK payment fetch
            # client.payment.fetch returns a dict representation of the response
            data = self.client.payment.fetch(payment_id)
            
            if not data or "id" not in data:
                logger.error("razorpay_get_payment_invalid_response", payment_id=payment_id)
                raise RazorpayAPIError(f"Empty or invalid payment details returned for ID: {payment_id}")

            return RazorpayPaymentDetails(
                id=data["id"],
                amount=data["amount"],
                currency=data.get("currency", "INR"),
                status=data["status"],
                method=data.get("method", "unknown"),
                failure_reason=data.get("error_description"),
                created_at=data["created_at"]
            )
            
        except RazorpayConfigError:
            # Reraise configuration errors directly
            raise
        except (errors.BadRequestError, errors.ServerError, errors.GatewayError) as e:
            # Handle SDK-specific errors
            logger.error("razorpay_sdk_error", payment_id=payment_id, error=str(e))
            raise RazorpayAPIError(f"Razorpay API call failed: {e}")
        except Exception as e:
            # Handle timeouts, network disconnects, or unexpected exceptions
            logger.error("razorpay_unexpected_api_error", payment_id=payment_id, error=str(e))
            raise RazorpayAPIError(f"Unexpected connection or parsing failure calling Razorpay API: {e}")

    def create_payment_link(
        self, 
        amount: int, 
        description: str, 
        reference_id: str,
        customer_name: str | None = None,
        customer_email: str | None = None,
        customer_phone: str | None = None
    ) -> RazorpayPaymentLinkDetails:
        """
        Creates a payment link using the official SDK capability.
        Used strictly as a low-level capability for future recovery execution phases.
        """
        logger.info(
            "razorpay_create_payment_link_started", 
            amount=amount, 
            reference_id=reference_id,
            customer_email=customer_email
        )
        
        payload = {
            "amount": amount,
            "currency": "INR",
            "accept_partial": False,
            "description": description,
            "reference_id": reference_id,
        }

        if customer_name or customer_email or customer_phone:
            customer_data = {}
            if customer_name:
                customer_data["name"] = customer_name
            if customer_email:
                customer_data["email"] = customer_email
            if customer_phone:
                customer_data["contact"] = customer_phone
            payload["customer"] = customer_data

        try:
            # Call official SDK payment link creation
            # client.payment_link.create takes a dictionary payload
            data = self.client.payment_link.create(data=payload)

            if not data or "id" not in data or "short_url" not in data:
                logger.error("razorpay_create_link_invalid_response", reference_id=reference_id)
                raise RazorpayAPIError("Invalid payment link response payload returned from Razorpay SDK.")

            return RazorpayPaymentLinkDetails(
                id=data["id"],
                short_url=data["short_url"],
                status=data["status"],
                reference_id=data.get("reference_id")
            )
            
        except RazorpayConfigError:
            raise
        except (errors.BadRequestError, errors.ServerError, errors.GatewayError) as e:
            logger.error("razorpay_sdk_link_error", reference_id=reference_id, error=str(e))
            raise RazorpayAPIError(f"Razorpay payment link creation failed: {e}")
        except Exception as e:
            logger.error("razorpay_unexpected_link_error", reference_id=reference_id, error=str(e))
            raise RazorpayAPIError(f"Unexpected connection failure creating Razorpay payment link: {e}")

    def verify_webhook_signature(self, payload_bytes: bytes, signature: str) -> None:
        """
        Verifies the signature of an incoming webhook payload using the official SDK.
        Raises RazorpayAPIError if verification fails, or RazorpayConfigError if secret is missing.
        """
        webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not webhook_secret:
            raise RazorpayConfigError(
                "RAZORPAY_WEBHOOK_SECRET is not configured. Please add it to your environment or .env file."
            )
        
        try:
            # We must pass the raw payload decoded to a string to verify_webhook_signature
            payload_str = payload_bytes.decode("utf-8")
            self.client.utility.verify_webhook_signature(
                payload_str,
                signature,
                webhook_secret
            )
            logger.info("razorpay_webhook_signature_verified")
        except RazorpayConfigError:
            raise
        except errors.SignatureVerificationError as e:
            logger.warning("razorpay_webhook_signature_invalid", error=str(e))
            raise RazorpayAPIError(f"Webhook signature verification failed: {e}")
        except Exception as e:
            logger.error("razorpay_webhook_verification_unexpected_error", error=str(e))
            raise RazorpayAPIError(f"Unexpected signature verification error: {e}")

