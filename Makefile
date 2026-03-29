.PHONY: local deploy-vpc deploy-storage deploy-cognito deploy-networking deploy-image deploy-ecs deploy-all destroy-dev destroy-prod destroy-all _destroy lint format typecheck test

ENV = dev

# Derived at make-time — no manual values needed
PROJECT_NAME   = $(shell grep '^name' pyproject.toml | head -1 | sed 's/name = "\(.*\)"/\1/')
STACK_PREFIX   = $(PROJECT_NAME)
ECR_REPO       = $(PROJECT_NAME)
AWS_ACCOUNT_ID = $(shell aws sts get-caller-identity --query Account --output text)
AWS_REGION     = $(shell aws configure get region)
GIT_SHA        = $(shell git rev-parse --short HEAD)
IMAGE_URI      = $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):$(GIT_SHA)
PARAMS         = $(shell jq -r '.[] | "\(.ParameterKey)=\(.ParameterValue)"' infra/params/$(ENV).json | tr '\n' ' ')
DOMAIN         = $(shell jq -r '.[] | select(.ParameterKey=="Domain") | .ParameterValue' infra/params/$(ENV).json)
ROOT_DOMAIN    = $(shell echo $(DOMAIN) | awk -F. '{print $$(NF-1)"."$$NF}')
HOSTED_ZONE_ID = $(shell aws route53 list-hosted-zones-by-name \
	--dns-name $(ROOT_DOMAIN) --query "HostedZones[0].Id" --output text | sed 's|/hostedzone/||')

local:
	docker-compose up --build

deploy-vpc:
	aws cloudformation deploy \
		--template-file infra/vpc.yaml \
		--stack-name $(STACK_PREFIX)-vpc-$(ENV) \
		--parameter-overrides $(PARAMS) \
		--capabilities CAPABILITY_NAMED_IAM

deploy-storage:
	aws cloudformation deploy \
		--template-file infra/storage.yaml \
		--stack-name $(STACK_PREFIX)-storage-$(ENV) \
		--parameter-overrides $(PARAMS) \
		--capabilities CAPABILITY_NAMED_IAM

deploy-cognito:
	aws cloudformation deploy \
		--template-file infra/cognito.yaml \
		--stack-name $(STACK_PREFIX)-cognito-$(ENV) \
		--parameter-overrides $(PARAMS) \
		--capabilities CAPABILITY_NAMED_IAM

deploy-cognito-secret:
	@USER_POOL_ID=$$(aws cloudformation describe-stacks \
		--stack-name $(STACK_PREFIX)-cognito-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
		--output text) && \
	CLIENT_ID=$$(aws cloudformation describe-stacks \
		--stack-name $(STACK_PREFIX)-cognito-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`AppClientId`].OutputValue' \
		--output text) && \
	CLIENT_SECRET=$$(aws cognito-idp describe-user-pool-client \
		--user-pool-id $$USER_POOL_ID \
		--client-id $$CLIENT_ID \
		--query 'UserPoolClient.ClientSecret' \
		--output text) && \
	aws ssm put-parameter \
		--name /conduit/$(ENV)/cognito-client-secret \
		--value $$CLIENT_SECRET \
		--type SecureString \
		--overwrite

deploy-networking:
	aws cloudformation deploy \
		--template-file infra/networking.yaml \
		--stack-name $(STACK_PREFIX)-networking-$(ENV) \
		--parameter-overrides $(PARAMS) HostedZoneId=$(HOSTED_ZONE_ID) \
		--capabilities CAPABILITY_NAMED_IAM

deploy-image:
	aws ecr describe-repositories --repository-names $(ECR_REPO) --region $(AWS_REGION) > /dev/null 2>&1 || \
		aws ecr create-repository --repository-name $(ECR_REPO) --region $(AWS_REGION)
	aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
	docker buildx build --platform linux/amd64 --push -t $(IMAGE_URI) -t $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO):latest .

deploy-ecs:
	aws cloudformation deploy \
		--template-file infra/ecs.yaml \
		--stack-name $(STACK_PREFIX)-ecs-$(ENV) \
		--parameter-overrides $(PARAMS) ImageUri=$(IMAGE_URI) \
		--capabilities CAPABILITY_NAMED_IAM

deploy-all: deploy-vpc deploy-storage deploy-cognito deploy-cognito-secret deploy-networking deploy-image deploy-ecs

destroy-dev: ENV = dev
destroy-dev: _destroy

destroy-prod: ENV = prod
destroy-prod: _destroy

destroy-all: destroy-dev destroy-prod

_destroy:
	-aws cloudformation delete-stack --stack-name $(STACK_PREFIX)-ecs-$(ENV)
	-aws cloudformation wait stack-delete-complete --stack-name $(STACK_PREFIX)-ecs-$(ENV)
	-aws cloudformation delete-stack --stack-name $(STACK_PREFIX)-networking-$(ENV)
	-aws cloudformation wait stack-delete-complete --stack-name $(STACK_PREFIX)-networking-$(ENV)
	-aws cloudformation delete-stack --stack-name $(STACK_PREFIX)-cognito-$(ENV)
	-aws cloudformation wait stack-delete-complete --stack-name $(STACK_PREFIX)-cognito-$(ENV)
	-aws cloudformation delete-stack --stack-name $(STACK_PREFIX)-storage-$(ENV)
	-aws cloudformation wait stack-delete-complete --stack-name $(STACK_PREFIX)-storage-$(ENV)
	-aws cloudformation delete-stack --stack-name $(STACK_PREFIX)-vpc-$(ENV)
	-aws cloudformation wait stack-delete-complete --stack-name $(STACK_PREFIX)-vpc-$(ENV)
	-aws ssm delete-parameter --name /conduit/$(ENV)/cognito-client-secret

lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	mypy src/

test:
	pytest
