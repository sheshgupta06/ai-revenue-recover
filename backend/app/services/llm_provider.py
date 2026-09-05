import json
import httpx
from typing import Optional
from app.core.config import settings
from app.core.logging import logger

# Reuse global client for HTTP Keep-Alive connection pooling (prevents TLS handshake timeouts)
_client = httpx.Client()

class LLMProviderException(Exception):
    """Custom exception representing LLM connection, timeout, or execution failures."""
    pass

class LLMProvider:
    @classmethod
    def generate_decision(cls, prompt: str, system_instructions: str) -> str:
        """
        Dispatches prompt execution to either the Mock generator or the live Gemini REST API.
        """
        provider = settings.LLM_PROVIDER.lower()

        if provider == "mock":
            return cls._generate_mock_response(prompt)
        elif provider == "gemini":
            return cls._generate_gemini_response(prompt, system_instructions)
        else:
            raise LLMProviderException(f"Unsupported LLM provider: {settings.LLM_PROVIDER}")

    @classmethod
    def _generate_mock_response(cls, prompt: str) -> str:
        """
        Generates a deterministic mock JSON response matching the analytical criteria of the prompt.
        Allows unit tests to run fully offline without live key requirements.
        """
        logger.info("llm_provider_executing_mock")
        
        prompt_lower = prompt.lower()
        
        # Default fallback mockup values
        action = "RETRY_NOW"
        delay_minutes = None
        confidence = 0.80
        reason = "UPI failure. Technical retry recommended."
        expected_probability = 0.50

        # Simulate decisions matching Phase 4 criteria
        if "bank_timeout" in prompt_lower or "network_failure" in prompt_lower:
            action = "RETRY_NOW"
            confidence = 0.90
            reason = "Temporary network timeout detected. Retry immediately."
            expected_probability = 0.95
        elif "insufficient_funds" in prompt_lower:
            action = "RETRY_LATER"
            delay_minutes = 120
            confidence = 0.85
            reason = "Soft debit decline. Recommending delay of 120 minutes."
            expected_probability = 0.70
        elif "expired_card" in prompt_lower:
            action = "PAYMENT_LINK"
            confidence = 0.80
            reason = "Credit card is expired. Recommending secure payment link for card update."
            expected_probability = 0.20
        elif "checkout_abandoned" in prompt_lower:
            action = "PAYMENT_LINK"
            confidence = 0.85
            reason = "Customer abandoned checkout. Send link to checkout resume."
            expected_probability = 0.45
        elif "overdue" in prompt_lower or "payment_terms_overdue" in prompt_lower:
            # Check if this is a repeat attempt (contains attempts >= 1)
            if "attempts" in prompt_lower and not ("attempts\": 0" in prompt_lower or "attempts: 0" in prompt_lower):
                action = "HUMAN_ESCALATION"
                confidence = 0.95
                reason = "B2B overdue invoice retry limit reached. Assigning collection agent."
                expected_probability = 0.10
            else:
                action = "PAYMENT_LINK"
                confidence = 0.90
                reason = "Invoice is overdue. Sending payment link reminder."
                expected_probability = 0.50

        # Return raw formatted JSON string
        return json.dumps({
            "action": action,
            "delay_minutes": delay_minutes,
            "confidence": confidence,
            "reason": reason,
            "expected_recovery_probability": expected_probability
        })

    @classmethod
    def _generate_gemini_response(cls, prompt: str, system_instructions: str) -> str:
        """
        Sends HTTP POST request directly to Gemini API REST endpoint.
        """
        api_key = settings.effective_ai_key
        if not api_key:
            raise LLMProviderException("Gemini API key is not configured. Define AI_API_KEY or GEMINI_API_KEY.")

        # Build url dynamically substituting parameters
        url = settings.LLM_API_URL.format(model=settings.LLM_MODEL_NAME, key=api_key)
        
        # Build standard Gemini generation request schema
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "systemInstruction": {
                "parts": [{"text": system_instructions}]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 1024,
            }
        }
        
        headers = {"Content-Type": "application/json"}

        logger.info(
            "llm_provider_sending_gemini_request", 
            model=settings.LLM_MODEL_NAME, 
            timeout=settings.LLM_TIMEOUT_SECONDS
        )

        def sanitize(text: str) -> str:
            if not text:
                return text
            if api_key:
                text = text.replace(api_key, "REDACTED")
            import re
            text = re.sub(r"key=[a-zA-Z0-9_\-]+", "key=REDACTED", text)
            return text

        try:
            response = _client.post(
                url, 
                json=payload, 
                headers=headers, 
                timeout=settings.LLM_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
            
            # Retrieve generated text contents
            candidates = data.get("candidates", [])
            if not candidates:
                raise LLMProviderException("Empty response: no candidates returned by Gemini.")
            
            content_parts = candidates[0].get("content", {}).get("parts", [])
            if not content_parts:
                raise LLMProviderException("Empty response: no content parts returned.")
            
            generated_text = content_parts[0].get("text", "")
            if not generated_text:
                raise LLMProviderException("Empty response text.")
            
            return generated_text.strip()

        except httpx.TimeoutException as te:
            err_msg = sanitize(str(te))
            logger.error("llm_provider_timeout", error=err_msg)
            raise LLMProviderException(f"LLM request timed out after {settings.LLM_TIMEOUT_SECONDS}s: {err_msg}")
        except httpx.HTTPStatusError as hse:
            err_msg = sanitize(str(hse))
            logger.error("llm_provider_status_error", status_code=hse.response.status_code, error=err_msg)
            raise LLMProviderException(f"Gemini API returned status {hse.response.status_code}: {err_msg}")
        except Exception as e:
            err_msg = sanitize(str(e))
            logger.error("llm_provider_error", error=err_msg)
            raise LLMProviderException(f"Unexpected LLM request error: {err_msg}")
