"""
sed.i AWS infrastructure — managed with Pulumi.

Layer 4: Bedrock IAM
  - sedi-bedrock-app: credentials for Railway production app
  - sedi-bedrock-dev: credentials for local development
  - Budget alarm at $20/month (set before enabling Bedrock traffic)

Layer 6 (not yet): S3 bucket for PDFs and media assets.

Run:
    cd infra && pulumi up
"""

import json
import pulumi
import pulumi_aws as aws

# ── Config ────────────────────────────────────────────────────────────────────
config = pulumi.Config()
env = pulumi.get_stack()  # "dev" | "prod"

# ── Bedrock IAM policy ────────────────────────────────────────────────────────
# Least-privilege: only InvokeModel on the specific Claude and Titan models we use.
# Extend this list when adding new models — don't use wildcard.
bedrock_policy_doc = aws.iam.get_policy_document(
    statements=[
        aws.iam.GetPolicyDocumentStatementArgs(
            effect="Allow",
            actions=["bedrock:InvokeModel"],
            resources=[
                # Claude Haiku 4.5 (fast chat, tagging)
                "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                # Claude Sonnet 4.5 (synthesis, MCP)
                "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-sonnet-4-5-20251001-v1:0",
                # Amazon Titan Embed v2 (embeddings)
                "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
            ],
        )
    ]
)

bedrock_policy = aws.iam.Policy(
    "sedi-bedrock-policy",
    name=f"sedi-bedrock-{env}",
    description="InvokeModel access for sed.i Bedrock LLM provider",
    policy=bedrock_policy_doc.json,
)

# ── App user (Railway production) ─────────────────────────────────────────────
app_user = aws.iam.User(
    "sedi-bedrock-app",
    name=f"sedi-bedrock-app-{env}",
    tags={"project": "sedi", "env": env, "purpose": "bedrock-app"},
)

aws.iam.UserPolicyAttachment(
    "sedi-bedrock-app-policy",
    user=app_user.name,
    policy_arn=bedrock_policy.arn,
)

app_access_key = aws.iam.AccessKey(
    "sedi-bedrock-app-key",
    user=app_user.name,
)

# ── Dev user (local development) ──────────────────────────────────────────────
dev_user = aws.iam.User(
    "sedi-bedrock-dev",
    name=f"sedi-bedrock-dev-{env}",
    tags={"project": "sedi", "env": env, "purpose": "bedrock-dev"},
)

aws.iam.UserPolicyAttachment(
    "sedi-bedrock-dev-policy",
    user=dev_user.name,
    policy_arn=bedrock_policy.arn,
)

dev_access_key = aws.iam.AccessKey(
    "sedi-bedrock-dev-key",
    user=dev_user.name,
)

# ── AWS Budget alarm ──────────────────────────────────────────────────────────
# IMPORTANT: set this up before routing any traffic to Bedrock.
# Alert at $20/month — adjust if call volume grows significantly.
budget_notification_email = config.get("budget_email") or "youenpu@gmail.com"

aws.budgets.Budget(
    "sedi-bedrock-budget",
    name=f"sedi-bedrock-monthly-{env}",
    budget_type="COST",
    limit_amount="20",
    limit_unit="USD",
    time_unit="MONTHLY",
    cost_filters=[
        aws.budgets.BudgetCostFilterArgs(
            name="Service",
            values=["Amazon Bedrock"],
        )
    ],
    notifications=[
        aws.budgets.BudgetNotificationArgs(
            comparison_operator="GREATER_THAN",
            threshold=80,
            threshold_type="PERCENTAGE",
            notification_type="ACTUAL",
            subscriber_email_addresses=[budget_notification_email],
        ),
        aws.budgets.BudgetNotificationArgs(
            comparison_operator="GREATER_THAN",
            threshold=100,
            threshold_type="PERCENTAGE",
            notification_type="FORECASTED",
            subscriber_email_addresses=[budget_notification_email],
        ),
    ],
)

# ── Outputs ───────────────────────────────────────────────────────────────────
# Access keys are sensitive — export as secrets so Pulumi encrypts them in state.
pulumi.export("app_access_key_id", app_access_key.id)
pulumi.export("app_secret_access_key", pulumi.Output.secret(app_access_key.secret))
pulumi.export("dev_access_key_id", dev_access_key.id)
pulumi.export("dev_secret_access_key", pulumi.Output.secret(dev_access_key.secret))
pulumi.export(
    "env_snippet",
    pulumi.Output.all(
        app_access_key.id, app_access_key.secret
    ).apply(
        lambda args: f"AWS_ACCESS_KEY_ID={args[0]}\nAWS_SECRET_ACCESS_KEY={args[1]}\nLLM_PROVIDER=bedrock"
    ),
)
