from __future__ import annotations

import html
import logging
import os
from datetime import datetime
from typing import Any

import requests


logger = logging.getLogger(__name__)


class EmailService:
    PLAN_DISPLAY_NAMES = {
        "TRIAL": "Enterprise Trial",
        "PROFESSIONAL": "Professional Monthly",
        "PROFESSIONAL_PLUS": "Professional Plus",
        "STEWARD_OPERATIONS": "Steward Operations",
        "GOVERNANCE_INTELLIGENCE": "Governance Intelligence",
        "ENTERPRISE_GOVERNANCE_PLATFORM": (
            "Enterprise Governance Platform"
        ),
    }

    def __init__(self) -> None:
        self.server_token = (
            os.getenv("POSTMARK_SERVER_TOKEN") or ""
        ).strip()

        self.from_email = (
            os.getenv("POSTMARK_FROM_EMAIL")
            or "welcome@admsdata.com"
        ).strip()

        self.from_name = (
            os.getenv("POSTMARK_FROM_NAME")
            or "AI Data Steward Copilot"
        ).strip()

        self.app_url = (
            os.getenv("APP_PUBLIC_URL")
            or (
                "https://steward-copilot-ui-503305938314."
                "us-east1.run.app/"
            )
        ).strip()

    def send_welcome_email(
        self,
        *,
        to_email: str,
        recipient_name: str,
        organization_name: str,
        plan_code: str,
        trial_end_date: datetime | None,
        enabled_domain: str,
        max_connections: int | None,
        max_monthly_explanations: int | None,
    ) -> dict[str, Any] | None:
        if not self.server_token:
            logger.warning(
                "Welcome email skipped because "
                "POSTMARK_SERVER_TOKEN is not configured."
            )
            return None

        normalized_plan = str(
            plan_code or ""
        ).strip().upper()

        plan_name = self.PLAN_DISPLAY_NAMES.get(
            normalized_plan,
            normalized_plan or "Current Plan",
        )

        recipient_display_name = (
            str(recipient_name or "").strip()
            or str(to_email).strip()
        )

        organization_display_name = (
            str(organization_name or "").strip()
            or "Your organization"
        )

        domain_display = (
            str(enabled_domain or "").strip().title()
            or "Not specified"
        )

        trial_end_display = (
            trial_end_date.strftime("%B %d, %Y")
            if trial_end_date
            else "Not applicable"
        )

        safe_name = html.escape(
            recipient_display_name
        )
        safe_org = html.escape(
            organization_display_name
        )
        safe_plan = html.escape(plan_name)
        safe_domain = html.escape(domain_display)
        safe_trial_end = html.escape(
            trial_end_display
        )
        safe_app_url = html.escape(
            self.app_url,
            quote=True,
        )

        connections_display = (
            str(max_connections)
            if max_connections is not None
            else "Plan limit"
        )

        explanations_display = (
            str(max_monthly_explanations)
            if max_monthly_explanations is not None
            else "Plan limit"
        )

        trial_row = ""

        if normalized_plan == "TRIAL":
            trial_row = f"""
                <p style="margin: 0;">
                  <strong>Trial ends:</strong>
                  {safe_trial_end}
                </p>
            """

        subject = (
            "Welcome to AI Data Steward Copilot®"
        )

        html_body = f"""
        <!doctype html>
        <html>
          <body
            style="
              margin: 0;
              padding: 0;
              background: #f8fafc;
              font-family: Arial, Helvetica, sans-serif;
              color: #173c72;
            "
          >
            <div
              style="
                max-width: 640px;
                margin: 0 auto;
                padding: 32px 20px;
              "
            >
              <div
                style="
                  background: #ffffff;
                  border: 1px solid #dbe5f1;
                  border-radius: 16px;
                  padding: 32px;
                "
              >
                <div
                  style="
                    font-size: 24px;
                    font-weight: 800;
                  "
                >
                  AI Data Steward Copilot®
                </div>

                <h1
                  style="
                    margin: 24px 0 12px;
                    font-size: 28px;
                  "
                >
                  Welcome, {safe_name}
                </h1>

                <p
                  style="
                    color: #475569;
                    font-size: 16px;
                    line-height: 1.6;
                  "
                >
                  Your organization workspace for
                  <strong>{safe_org}</strong>
                  has been created successfully.
                </p>

                <div
                  style="
                    background: #f1f5f9;
                    border-radius: 12px;
                    padding: 20px;
                    margin: 24px 0;
                  "
                >
                  <p style="margin: 0 0 10px;">
                    <strong>Plan:</strong>
                    {safe_plan}
                  </p>

                  <p style="margin: 0 0 10px;">
                    <strong>Data domain:</strong>
                    {safe_domain}
                  </p>

                  <p style="margin: 0 0 10px;">
                    <strong>Connections included:</strong>
                    {connections_display}
                  </p>

                  <p style="margin: 0 0 10px;">
                    <strong>
                      Monthly AI match explanations:
                    </strong>
                    {explanations_display}
                  </p>

                  {trial_row}
                </div>

                <p
                  style="
                    color: #475569;
                    font-size: 16px;
                    line-height: 1.6;
                  "
                >
                  Your next step is to connect your
                  first enterprise data source.
                </p>

                <div style="margin-top: 28px;">
                  <a
                    href="{safe_app_url}"
                    style="
                      display: inline-block;
                      background: #2563eb;
                      color: #ffffff;
                      text-decoration: none;
                      font-weight: 700;
                      padding: 13px 20px;
                      border-radius: 10px;
                    "
                  >
                    Open AI Data Steward Copilot
                  </a>
                </div>

                <p
                  style="
                    margin-top: 32px;
                    color: #64748b;
                    font-size: 13px;
                  "
                >
                  Trial enrollment does not represent
                  a paid charge.
                </p>
              </div>
            </div>
          </body>
        </html>
        """

        text_body = (
            f"Welcome to AI Data Steward Copilot®, "
            f"{recipient_display_name}.\n\n"
            f"Organization: {organization_display_name}\n"
            f"Plan: {plan_name}\n"
            f"Data domain: {domain_display}\n"
            f"Connections included: "
            f"{connections_display}\n"
            f"Monthly AI match explanations: "
            f"{explanations_display}\n"
        )

        if normalized_plan == "TRIAL":
            text_body += (
                f"Trial ends: {trial_end_display}\n"
            )

        text_body += (
            "\nYour next step is to connect your "
            "first enterprise data source.\n\n"
            f"{self.app_url}\n"
        )

        response = requests.post(
            "https://api.postmarkapp.com/email",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": (
                    self.server_token
                ),
            },
            json={
                "From": (
                    f"{self.from_name} "
                    f"<{self.from_email}>"
                ),
                "To": to_email,
                "Subject": subject,
                "HtmlBody": html_body,
                "TextBody": text_body,
                "MessageStream": "outbound",
                "Tag": "welcome-email",
            },
            timeout=15,
        )

        response.raise_for_status()

        result = response.json()

        logger.info(
            "Welcome email submitted to Postmark "
            "recipient=%s plan_code=%s message_id=%s",
            to_email,
            normalized_plan,
            result.get("MessageID"),
        )

        return result

    def send_user_invitation(
      self,
      *,
      to_email: str,
      invite_sender_name: str,
      invite_sender_organization_name: str,
      action_url: str,
  ) -> dict[str, Any] | None:
      if not self.server_token:
          logger.warning(
              "User invitation email skipped because "
              "POSTMARK_SERVER_TOKEN is not configured."
          )
          return None

      model = {
          "invite_sender_name": (
              str(invite_sender_name or "").strip()
              or "Your organization administrator"
          ),
          "invite_sender_organization_name": (
              str(
                  invite_sender_organization_name
                  or ""
              ).strip()
              or "Your organization"
          ),
          "product_name": "AI Data Steward Copilot®",
          "product_url": self.app_url,
          "action_url": action_url,
          "company_name": (
              "Advanced Data Management Solutions"
          ),
          "company_address": (
              "United States"
          ),
      }

      response = requests.post(
          "https://api.postmarkapp.com/email/withTemplate",
          headers={
              "Accept": "application/json",
              "Content-Type": "application/json",
              "X-Postmark-Server-Token": (
                  self.server_token
              ),
          },
          json={
              "From": (
                  f"{self.from_name} "
                  f"<{self.from_email}>"
              ),
              "To": to_email,
              "TemplateAlias": "user-invitation",
              "TemplateModel": model,
              "MessageStream": "invite-users",
              "Tag": "user-invitation",
          },
          timeout=15,
      )

      response.raise_for_status()

      result = response.json()

      logger.info(
          "User invitation email submitted "
          "to Postmark recipient=%s message_id=%s",
          to_email,
          result.get("MessageID"),
      )

      return result
    def send_custom_connection_request_notification(
      self,
      *,
      request_id: str,
      organization_id: str,
      customer_id: str,
      requested_by: str,
      contact_email: str,
      vendor_name: str,
      connection_type: str,
      environment: str,
      auth_preference: str | None,
      read_data_required: bool,
      metadata_required: bool,
      use_case: str | None,
  ) -> dict[str, Any] | None:

      if not self.server_token:
          logger.warning(
              "Custom connection request email skipped because "
              "POSTMARK_SERVER_TOKEN is not configured."
          )
          return None

      notification_email = (
          os.getenv("CUSTOM_CONNECTION_NOTIFICATION_EMAIL")
          or "support@admsdata.com"
      ).strip()

      safe_request_id = html.escape(
          str(request_id or "")
      )
      safe_organization_id = html.escape(
          str(organization_id or "")
      )
      safe_customer_id = html.escape(
          str(customer_id or "")
      )
      safe_requested_by = html.escape(
          str(requested_by or "")
      )
      safe_contact_email = html.escape(
          str(contact_email or "")
      )
      safe_vendor_name = html.escape(
          str(vendor_name or "")
      )
      safe_connection_type = html.escape(
          str(connection_type or "")
      )
      safe_environment = html.escape(
          str(environment or "")
      )
      safe_auth_preference = html.escape(
          str(auth_preference or "Not specified")
      )
      safe_use_case = html.escape(
          str(use_case or "Not provided")
      )

      read_data_display = (
          "Yes"
          if read_data_required
          else "No"
      )

      metadata_display = (
          "Yes"
          if metadata_required
          else "No"
      )

      subject = (
          "New Custom Connection Request - "
          f"{vendor_name}"
      )

      html_body = f"""
      <!doctype html>
      <html>
        <body
          style="
            margin: 0;
            padding: 0;
            background: #f8fafc;
            font-family: Arial, Helvetica, sans-serif;
            color: #173c72;
          "
        >
          <div
            style="
              max-width: 680px;
              margin: 0 auto;
              padding: 32px 20px;
            "
          >
            <div
              style="
                background: #ffffff;
                border: 1px solid #dbe5f1;
                border-radius: 16px;
                padding: 32px;
              "
            >
              <div
                style="
                  font-size: 22px;
                  font-weight: 800;
                "
              >
                AI Data Steward Copilot®
              </div>

              <h1
                style="
                  margin: 24px 0 12px;
                  font-size: 26px;
                "
              >
                New Custom Connection Request
              </h1>

              <p
                style="
                  color: #475569;
                  line-height: 1.6;
                "
              >
                A customer has requested support for a
                custom enterprise connection.
              </p>

              <div
                style="
                  margin-top: 24px;
                  padding: 20px;
                  border-radius: 12px;
                  background: #f1f5f9;
                "
              >
                <p>
                  <strong>Request ID:</strong>
                  {safe_request_id}
                </p>

                <p>
                  <strong>Organization ID:</strong>
                  {safe_organization_id}
                </p>

                <p>
                  <strong>Customer ID:</strong>
                  {safe_customer_id}
                </p>

                <p>
                  <strong>Requested By:</strong>
                  {safe_requested_by}
                </p>

                <p>
                  <strong>Contact Email:</strong>
                  {safe_contact_email}
                </p>

                <p>
                  <strong>Vendor / Platform:</strong>
                  {safe_vendor_name}
                </p>

                <p>
                  <strong>Connection Type:</strong>
                  {safe_connection_type}
                </p>

                <p>
                  <strong>Environment:</strong>
                  {safe_environment}
                </p>

                <p>
                  <strong>Authentication Preference:</strong>
                  {safe_auth_preference}
                </p>

                <p>
                  <strong>Read Data Required:</strong>
                  {read_data_display}
                </p>

                <p>
                  <strong>Metadata Required:</strong>
                  {metadata_display}
                </p>

                <p style="margin-bottom: 0;">
                  <strong>Use Case:</strong><br />
                  {safe_use_case}
                </p>
              </div>

              <p
                style="
                  margin-top: 24px;
                  color: #64748b;
                  font-size: 13px;
                "
              >
                No credentials or secrets are included
                in this notification.
              </p>
            </div>
          </div>
        </body>
      </html>
      """

      text_body = (
          "New Custom Connection Request\n\n"
          f"Request ID: {request_id}\n"
          f"Organization ID: {organization_id}\n"
          f"Customer ID: {customer_id}\n"
          f"Requested By: {requested_by}\n"
          f"Contact Email: {contact_email}\n"
          f"Vendor / Platform: {vendor_name}\n"
          f"Connection Type: {connection_type}\n"
          f"Environment: {environment}\n"
          f"Authentication Preference: "
          f"{auth_preference or 'Not specified'}\n"
          f"Read Data Required: {read_data_display}\n"
          f"Metadata Required: {metadata_display}\n"
          f"Use Case: {use_case or 'Not provided'}\n\n"
          "No credentials or secrets are included "
          "in this notification."
      )

      response = requests.post(
          "https://api.postmarkapp.com/email",
          headers={
              "Accept": "application/json",
              "Content-Type": "application/json",
              "X-Postmark-Server-Token": (
                  self.server_token
              ),
          },
          json={
              "From": (
                  f"{self.from_name} "
                  f"<{self.from_email}>"
              ),
              "To": notification_email,
              "ReplyTo": contact_email,
              "Subject": subject,
              "HtmlBody": html_body,
              "TextBody": text_body,
              "MessageStream": "outbound",
              "Tag": "custom-connection-request",
          },
          timeout=15,
      )

      response.raise_for_status()

      result = response.json()

      logger.info(
          "Custom connection request notification "
          "submitted to Postmark "
          "request_id=%s vendor=%s "
          "recipient=%s message_id=%s",
          request_id,
          vendor_name,
          notification_email,
          result.get("MessageID"),
      )

      return result