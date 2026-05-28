"""
sed.i AWS infrastructure — managed with Pulumi.

Layer 4: Bedrock IAM
  - sedi-bedrock-app: credentials for Railway production app
  - sedi-bedrock-dev: credentials for local development
  - Budget alarm at $20/month (set before enabling Bedrock traffic)

Layer 6: S3 object storage
  - Private bucket for PDFs and media, SSE-S3 encrypted
  - Lifecycle: Standard → IA after 90 days → Glacier after 365
  - App IAM user extended with s3:PutObject / s3:GetObject on the bucket

Run:
    cd infra && pulumi up
"""

import json
import pulumi
import pulumi_aws as aws

# ── Config ────────────────────────────────────────────────────────────────────
config = pulumi.Config()
env = pulumi.get_stack()  # "dev" | "prod"

# Account ID needed for inference-profile ARNs (cross-region profiles use account-scoped ARNs)
account_id = aws.get_caller_identity().account_id

# ── Bedrock IAM policy ────────────────────────────────────────────────────────
# Cross-region inference profiles (us.*) require two ARN forms:
#   - foundation-model: what the model physically is
#   - inference-profile: what IAM checks when the Converse API routes the call
bedrock_policy = aws.iam.Policy(
    "sedi-bedrock-policy",
    name=f"sedi-bedrock-{env}",
    description="InvokeModel access for sed.i Bedrock LLM provider",
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [
                    # Foundation model ARNs (wildcard region — available in all Bedrock regions)
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0",   # tagging + insight
                    "arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",    # summary + mcp_summary
                    # Titan v1 (1536-dim) is the configured embed model (_EMBED_MODEL_BEDROCK in llm_client.py)
                    # v2 has a different vector dimension — adding both avoids a hard break if migrated later
                    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v1:0",
                    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
                    # Cross-region inference profile ARNs — IAM checks these when model ID is prefixed us.*
                    # claude-sonnet-4-5-20250929 is used for SQL generation (LLM_MODEL_SQL_GEN_BEDROCK)
                    f"arn:aws:bedrock:us-east-2:{account_id}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    f"arn:aws:bedrock:us-east-2:{account_id}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                    # Nova cross-region profiles — permit if model IDs are prefixed us.amazon.* in future
                    f"arn:aws:bedrock:us-east-2:{account_id}:inference-profile/us.amazon.nova-micro-v1:0",
                    f"arn:aws:bedrock:us-east-2:{account_id}:inference-profile/us.amazon.nova-lite-v1:0",
                ],
            }
        ],
    }),
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

# ── S3 bucket (Layer 6) ───────────────────────────────────────────────────────
# Bucket names are globally unique across all AWS accounts. A fixed low-entropy
# name like "sedi-assets-dev" can collide with another account's bucket, causing
# Pulumi to fail. Include the account ID (12 digits, globally unique) to guarantee
# uniqueness without needing a random suffix that changes on each deploy.
bucket = aws.s3.BucketV2(
    "sedi-assets",
    bucket=f"sedi-assets-{env}-{account_id}",
    tags={"project": "sedi", "env": env},
)

# Block all public access — content is served only via presigned URLs
aws.s3.BucketPublicAccessBlock(
    "sedi-assets-block-public",
    bucket=bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

# SSE-S3 encryption (no KMS cost, sufficient for this data sensitivity)
aws.s3.BucketServerSideEncryptionConfigurationV2(
    "sedi-assets-sse",
    bucket=bucket.id,
    rules=[
        aws.s3.BucketServerSideEncryptionConfigurationV2RuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationV2RuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256",
            ),
        )
    ],
)

# Lifecycle: Standard → IA after 90 days → Glacier after 365 days
aws.s3.BucketLifecycleConfigurationV2(
    "sedi-assets-lifecycle",
    bucket=bucket.id,
    rules=[
        aws.s3.BucketLifecycleConfigurationV2RuleArgs(
            id="tiered-storage",
            status="Enabled",
            transitions=[
                aws.s3.BucketLifecycleConfigurationV2RuleTransitionArgs(
                    days=90,
                    storage_class="STANDARD_IA",
                ),
                aws.s3.BucketLifecycleConfigurationV2RuleTransitionArgs(
                    days=365,
                    storage_class="GLACIER",
                ),
            ],
        )
    ],
)

# Build S3 policy inline — get_policy_document can't accept Output args without deadlocking
s3_policy_json = bucket.arn.apply(
    lambda arn: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                "Resource": f"{arn}/*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": arn,
            },
        ],
    })
)

s3_policy = aws.iam.Policy(
    "sedi-s3-policy",
    name=f"sedi-s3-{env}",
    description="S3 read/write access for sed.i assets bucket",
    policy=s3_policy_json,
)

aws.iam.UserPolicyAttachment(
    "sedi-bedrock-app-s3",
    user=app_user.name,
    policy_arn=s3_policy.arn,
)

aws.iam.UserPolicyAttachment(
    "sedi-bedrock-dev-s3",
    user=dev_user.name,
    policy_arn=s3_policy.arn,
)

# ── Outputs ───────────────────────────────────────────────────────────────────
# Access keys are sensitive — export as secrets so Pulumi encrypts them in state.
pulumi.export("app_access_key_id", app_access_key.id)
pulumi.export("app_secret_access_key", pulumi.Output.secret(app_access_key.secret))
pulumi.export("dev_access_key_id", dev_access_key.id)
pulumi.export("dev_secret_access_key", pulumi.Output.secret(dev_access_key.secret))
pulumi.export("s3_bucket_name", bucket.id)
pulumi.export(
    "env_snippet",
    pulumi.Output.all(app_access_key.id, app_access_key.secret, bucket.id).apply(
        lambda args: (
            f"AWS_ACCESS_KEY_ID={args[0]}\n"
            f"AWS_SECRET_ACCESS_KEY={args[1]}\n"
            f"AWS_S3_BUCKET={args[2]}\n"
            f"LLM_PROVIDER=bedrock"
        )
    ),
)
