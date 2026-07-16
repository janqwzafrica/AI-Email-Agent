"""
brevo.py
--------
Lightweight wrapper around the Brevo (Sendinblue) REST API covering:

    Action                      Endpoint
    ----------------------------------------------------------------
    Create email template       POST /v3/smtp/templates
    Update / activate template  PUT  /v3/smtp/templates/{templateId}
    Create email campaign       POST /v3/emailCampaigns
    Send campaign immediately   POST /v3/emailCampaigns/{campaignId}/sendNow
    Send test email             POST /v3/emailCampaigns/{campaignId}/sendTest
    Get campaign stats/report   GET  /v3/emailCampaigns/{campaignId}
    Email report to stakeholders POST /v3/smtp/email (transactional)
    Send full report            POST /v3/emailCampaigns/{campaignId}/sendReport
    Manage contact lists        POST /v3/contacts/lists | GET /v3/contacts/lists
    Import contacts             POST /v3/contacts/import
    Unsubscribe a contact       PUT  /v3/contacts/{email}

Setup
-----
Requires the `requests` package, and a Brevo API key available in the
environment as BREVO_API_KEY (e.g. in a .env file loaded with python-dotenv,
or exported in your shell):

    export BREVO_API_KEY="xkeysib-xxxxxxxx"

Usage
-----
    from brevo import BrevoClient

    brevo = BrevoClient()

    template = brevo.create_email_template(
        template_name="Welcome Email",
        subject="Welcome to our platform!",
        sender_name="Acme Inc",
        sender_email="hello@acme.com",
        html_content="<html><body><h1>Welcome!</h1></body></html>",
    )
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

BREVO_BASE_URL = "https://api.brevo.com/v3"


class BrevoAPIError(Exception):
    """Raised when the Brevo API returns an error response."""

    def __init__(self, status_code: int, message: str, payload: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Brevo API error {status_code}: {message}")


class BrevoClient:
    """Minimal client for the Brevo transactional email / campaigns API."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = BREVO_BASE_URL, timeout: int = 30):
        self.api_key = api_key or os.environ.get("BREVO_API_KEY") or os.environ.get("BREVO_API")
        if not self.api_key:
            raise ValueError(
                "Brevo API key not found. Set the BREVO_API_KEY environment variable "
                "or pass api_key= explicitly when creating BrevoClient()."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ #
    # Internal request helper
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, json=json, params=params, timeout=self.timeout)

        if not response.ok:
            try:
                payload = response.json()
                message = payload.get("message", response.text)
            except ValueError:
                payload = None
                message = response.text
            raise BrevoAPIError(response.status_code, message, payload)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------ #
    # Email templates
    # ------------------------------------------------------------------ #
    def create_email_template(
        self,
        template_name: str,
        subject: str,
        sender_name: str,
        sender_email: str,
        html_content: str,
        is_active: bool = False,
        reply_to: Optional[str] = None,
        to_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /v3/smtp/templates - Create a new email template."""
        body: Dict[str, Any] = {
            "templateName": template_name,
            "subject": subject,
            "sender": {"name": sender_name, "email": sender_email},
            "htmlContent": html_content,
            "isActive": is_active,
        }
        if reply_to:
            body["replyTo"] = reply_to
        if to_field:
            body["toField"] = to_field
        return self._request("POST", "/smtp/templates", json=body)

    def update_email_template(
        self,
        template_id: int,
        template_name: Optional[str] = None,
        subject: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_email: Optional[str] = None,
        html_content: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> None:
        """PUT /v3/smtp/templates/{templateId} - Update and/or activate a template."""
        body: Dict[str, Any] = {}
        if template_name is not None:
            body["templateName"] = template_name
        if subject is not None:
            body["subject"] = subject
        if sender_name is not None or sender_email is not None:
            body["sender"] = {}
            if sender_name is not None:
                body["sender"]["name"] = sender_name
            if sender_email is not None:
                body["sender"]["email"] = sender_email
        if html_content is not None:
            body["htmlContent"] = html_content
        if is_active is not None:
            body["isActive"] = is_active

        return self._request("PUT", f"/smtp/templates/{template_id}", json=body)

    def activate_email_template(self, template_id: int) -> None:
        """Convenience wrapper: activate a template via update endpoint."""
        return self.update_email_template(template_id, is_active=True)

    # ------------------------------------------------------------------ #
    # Email campaigns
    # ------------------------------------------------------------------ #
    def create_email_campaign(
        self,
        name: str,
        subject: str,
        sender_name: str,
        sender_email: str,
        html_content: Optional[str] = None,
        template_id: Optional[int] = None,
        list_ids: Optional[List[int]] = None,
        scheduled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /v3/emailCampaigns - Create a new email campaign.

        Provide either html_content or template_id (Brevo requires content
        or a linked template).
        """
        body: Dict[str, Any] = {
            "name": name,
            "subject": subject,
            "sender": {"name": sender_name, "email": sender_email},
        }
        if html_content is not None:
            body["htmlContent"] = html_content
        if template_id is not None:
            body["templateId"] = template_id
        if list_ids is not None:
            body["recipients"] = {"listIds": list_ids}
        if scheduled_at is not None:
            body["scheduledAt"] = scheduled_at

        return self._request("POST", "/emailCampaigns", json=body)

    def get_email_campaigns(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v3/emailCampaigns - Retrieve all email campaigns (with statistics)."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset, "statistics": "globalStats"}
        if status is not None:
            params["status"] = status
        return self._request("GET", "/emailCampaigns", params=params)

    def delete_email_campaign(self, campaign_id: int) -> None:
        """DELETE /v3/emailCampaigns/{campaignId} - Delete an email campaign."""
        return self._request("DELETE", f"/emailCampaigns/{campaign_id}")

    def send_campaign_now(self, campaign_id: int) -> None:
        """POST /v3/emailCampaigns/{campaignId}/sendNow - Send campaign immediately."""
        return self._request("POST", f"/emailCampaigns/{campaign_id}/sendNow")

    def send_test_email(self, campaign_id: int, emails: List[str]) -> None:
        """POST /v3/emailCampaigns/{campaignId}/sendTest - Send a test email."""
        return self._request("POST", f"/emailCampaigns/{campaign_id}/sendTest", json={"emailTo": emails})

    def get_campaign_report(self, campaign_id: int) -> Dict[str, Any]:
        """GET /v3/emailCampaigns/{campaignId} - Get campaign stats / report."""
        return self._request("GET", f"/emailCampaigns/{campaign_id}")

    def send_campaign_report(
        self,
        campaign_id: int,
        emails: List[str],
        subject: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        """POST /v3/emailCampaigns/{campaignId}/sendReport - Send the full campaign report."""
        body: Dict[str, Any] = {"email": {"recipients": emails}}
        if subject is not None:
            body["email"]["subject"] = subject
        if message is not None:
            body["email"]["message"] = message
        return self._request("POST", f"/emailCampaigns/{campaign_id}/sendReport", json=body)

    # ------------------------------------------------------------------ #
    # Transactional email (e.g. reports to stakeholders)
    # ------------------------------------------------------------------ #
    def send_transactional_email(
        self,
        to: List[Dict[str, str]],
        subject: str,
        sender_name: str,
        sender_email: str,
        html_content: Optional[str] = None,
        template_id: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """POST /v3/smtp/email - Send a transactional email.

        Useful for emailing ad-hoc reports to stakeholders.
        `to` is a list like [{"email": "person@example.com", "name": "Person"}].
        """
        body: Dict[str, Any] = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": to,
            "subject": subject,
        }
        if html_content is not None:
            body["htmlContent"] = html_content
        if template_id is not None:
            body["templateId"] = template_id
        if params is not None:
            body["params"] = params
        if attachments is not None:
            body["attachment"] = attachments

        return self._request("POST", "/smtp/email", json=body)

    # ------------------------------------------------------------------ #
    # Contact lists
    # ------------------------------------------------------------------ #
    def create_contact_list(self, list_name: str, folder_id: int) -> Dict[str, Any]:
        """POST /v3/contacts/lists - Create a new contact list."""
        return self._request("POST", "/contacts/lists", json={"name": list_name, "folderId": folder_id})

    def get_contact_lists(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """GET /v3/contacts/lists - Retrieve all contact lists."""
        return self._request("GET", "/contacts/lists", params={"limit": limit, "offset": offset})

    def get_contact_list(self, list_id: int) -> Dict[str, Any]:
        """GET /v3/contacts/lists/{listId} - Retrieve details of a single list."""
        return self._request("GET", f"/contacts/lists/{list_id}")

    def delete_contact_list(self, list_id: int) -> None:
        """DELETE /v3/contacts/lists/{listId} - Delete a contact list."""
        return self._request("DELETE", f"/contacts/lists/{list_id}")

    def get_contacts_from_list(
        self,
        list_id: int,
        limit: int = 500,
        offset: int = 0,
        sort: str = "desc",
    ) -> Dict[str, Any]:
        """GET /v3/contacts/lists/{listId}/contacts - Contacts belonging to a list."""
        return self._request(
            "GET",
            f"/contacts/lists/{list_id}/contacts",
            params={"limit": limit, "offset": offset, "sort": sort},
        )

    def remove_contacts_from_list(self, list_id: int, emails: List[str]) -> Dict[str, Any]:
        """POST /v3/contacts/lists/{listId}/contacts/remove - Remove contacts from a list."""
        return self._request(
            "POST",
            f"/contacts/lists/{list_id}/contacts/remove",
            json={"emails": emails},
        )

    def import_contacts(
        self,
        list_ids: List[int],
        file_url: Optional[str] = None,
        file_body: Optional[str] = None,
        email_blacklist: bool = False,
        sms_blacklist: bool = False,
        update_existing: bool = True,
    ) -> Dict[str, Any]:
        """POST /v3/contacts/import - Bulk import contacts into one or more lists.

        Provide either file_url (a hosted CSV) or file_body (raw CSV text).
        """
        body: Dict[str, Any] = {
            "listIds": list_ids,
            "emailBlacklist": email_blacklist,
            "smsBlacklist": sms_blacklist,
            "updateExistingContacts": update_existing,
        }
        if file_url is not None:
            body["fileUrl"] = file_url
        if file_body is not None:
            body["fileBody"] = file_body
        return self._request("POST", "/contacts/import", json=body)

    def unsubscribe_contact(self, email: str) -> None:
        """PUT /v3/contacts/{email} - Unsubscribe a contact (sets emailBlacklisted: true)."""
        return self._request("PUT", f"/contacts/{email}", json={"emailBlacklisted": True})


if __name__ == "__main__":
    # Simple smoke test / usage example.
    # Requires BREVO_API_KEY to be set in the environment.
    client = BrevoClient()
    print("Fetching contact lists...")
    lists = client.get_contact_lists()
    print(lists)