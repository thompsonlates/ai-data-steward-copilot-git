from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from app.services.email_service import EmailService


load_dotenv()

service = EmailService()

try:
    result = service.send_welcome_email(
        to_email="thompsonlates@gmail.com",
        recipient_name="Matthew",
        organization_name="Advanced Data Management Solutions",
        plan_code="TRIAL",
        trial_end_date=(
            datetime.now(timezone.utc)
            + timedelta(days=14)
        ),
        enabled_domain="CUSTOMER",
        max_connections=2,
        max_monthly_explanations=100,
    )

    print(result)

except requests.exceptions.HTTPError as exc:
    print("STATUS:", exc.response.status_code)
    print("POSTMARK RESPONSE:")
    print(exc.response.text)
