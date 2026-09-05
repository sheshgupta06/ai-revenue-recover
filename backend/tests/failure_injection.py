import httpx
import razorpay
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

class FailureInjector:
    @staticmethod
    @contextmanager
    def mock_gemini_timeout():
        """
        Mocks the httpx Client post method to simulate an httpx.TimeoutException
        when hitting the Gemini API endpoint.
        """
        # Note: llm_provider.py uses httpx.Client().post()
        # We patch httpx.Client.send or httpx.Client.post
        with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Gemini connection timed out")):
            yield

    @staticmethod
    @contextmanager
    def mock_gemini_http_error(status_code=500):
        """
        Mocks the httpx Client post method to raise an httpx.HTTPStatusError
        simulating a downstream server error (500 or 503).
        """
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
        response = httpx.Response(status_code, request=request)
        error = httpx.HTTPStatusError(f"HTTP Error {status_code}", request=request, response=response)
        with patch("httpx.Client.post", side_effect=error):
            yield

    @staticmethod
    @contextmanager
    def mock_gemini_malformed_json():
        """
        Mocks the httpx Client response to return malformed or empty/invalid JSON content
        to verify LLM provider parsing robust fallbacks.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Return invalid JSON text (malformed schema)
        mock_resp.text = "{invalid json: true"
        mock_resp.json.side_effect = ValueError("Invalid JSON syntax")
        with patch("httpx.Client.post", return_value=mock_resp):
            yield

    @staticmethod
    @contextmanager
    def mock_razorpay_timeout():
        """
        Mocks the Razorpay SDK client methods to raise an httpx.TimeoutException.
        """
        with patch("razorpay.resources.Payment.fetch", side_effect=httpx.TimeoutException("Razorpay payment fetch timed out")), \
             patch("razorpay.resources.PaymentLink.create", side_effect=httpx.TimeoutException("Razorpay link creation timed out")):
            yield

    @staticmethod
    @contextmanager
    def mock_razorpay_connect_error():
        """
        Mocks the Razorpay SDK client methods to raise an httpx.ConnectError.
        """
        with patch("razorpay.resources.Payment.fetch", side_effect=httpx.ConnectError("Failed to connect to Razorpay")), \
             patch("razorpay.resources.PaymentLink.create", side_effect=httpx.ConnectError("Failed to connect to Razorpay")):
            yield

    @staticmethod
    @contextmanager
    def mock_razorpay_api_error():
        """
        Mocks Razorpay SDK calls to return a generic BadRequestError.
        """
        from razorpay.errors import BadRequestError
        with patch("razorpay.resources.Payment.fetch", side_effect=BadRequestError("Bad Request")), \
             patch("razorpay.resources.PaymentLink.create", side_effect=BadRequestError("Bad Request")):
            yield
