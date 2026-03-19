.PHONY: local deploy-storage deploy-cognito deploy-networking deploy-ecs deploy-all lint format typecheck test

ENV ?= dev
STACK_PREFIX = conduit

local:
	docker-compose up --build

deploy-storage:
	aws cloudformation deploy \
		--template-file infra/storage.yaml \
		--stack-name $(STACK_PREFIX)-storage-$(ENV) \
		--parameter-overrides file://infra/params/$(ENV).json \
		--capabilities CAPABILITY_NAMED_IAM

deploy-cognito:
	aws cloudformation deploy \
		--template-file infra/cognito.yaml \
		--stack-name $(STACK_PREFIX)-cognito-$(ENV) \
		--parameter-overrides file://infra/params/$(ENV).json \
		--capabilities CAPABILITY_NAMED_IAM

deploy-networking:
	aws cloudformation deploy \
		--template-file infra/networking.yaml \
		--stack-name $(STACK_PREFIX)-networking-$(ENV) \
		--parameter-overrides file://infra/params/$(ENV).json \
		--capabilities CAPABILITY_NAMED_IAM

deploy-ecs:
	aws cloudformation deploy \
		--template-file infra/ecs.yaml \
		--stack-name $(STACK_PREFIX)-ecs-$(ENV) \
		--parameter-overrides file://infra/params/$(ENV).json \
		--capabilities CAPABILITY_NAMED_IAM

deploy-all: deploy-storage deploy-cognito deploy-networking deploy-ecs

lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	mypy src/

test:
	pytest
