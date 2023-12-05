.DEFAULT_GOAL:=help
BUILDER_IMAGE_NAME ?= mirrors.tencent.com/bkpaas/heroku-builder
BUILDER_TAG ?= bionic
HEROKU_IMAGE_NAME ?= mirrors.tencent.com/bkpaas/heroku-builder-all-in-one
IMAGE_TAG ?= bionic

ifeq ($(shell go env GOOS),windows)
BINARY_SUFFIX := .exe
else
BINARY_SUFFIX :=
endif

##@ Build Binary
.PHONY: build
build: build-init lifecycle-driver entrypoint  ## Build all binary


.PHONY: build-init
build-init: $(LOCALBIN)  ## Build `build-init` binary
	go build -o $(LOCALBIN)/build-init$(BINARY_SUFFIX) ./cmd/build-init


.PHONY: lifecycle-driver
lifecycle-driver: $(LOCALBIN)  ## Build `lifecycle-driver` binary
	go build -o $(LOCALBIN)/lifecycle-driver$(BINARY_SUFFIX) ./cmd/lifecycle-driver


.PHONY: entrypoint
entrypoint: $(LOCALBIN) ## Build `entrypoint` binary
	go build -o $(LOCALBIN)/entrypoint ./cmd/entrypoint

##@ Build Image
.PHONY: heroku-builder
heroku-builder:  ## Build `heroku-builder` image
	IMAGE_NAME=$(HEROKU_IMAGE_NAME) IMAGE_TAG=$(IMAGE_TAG) docker buildx bake heroku-builder -f docker-build/heroku-builder/docker-bake.hcl


##@ Test
.PHONY: test
test: ## Run unittest
	go test ./...

.PHONY: integration
integration:  ## Run Intergration Test


##@ Misc
.PHONY: lint
lint: install-golangci-lint ## Run golangci-lint
	$(GOCILINT) run


## Location to build binary or install dependencies to
LOCALBIN ?= $(shell pwd)/bin
$(LOCALBIN):
	@mkdir -p $(LOCALBIN)


GOCILINT ?= $(LOCALBIN)/golangci-lint
.PHONY: install-golangci-lint
install-golangci-lint: $(LOCALBIN)
	GOBIN=$(LOCALBIN) go install github.com/golangci/golangci-lint/cmd/golangci-lint@v1.53.3


.PHONY: help
help:  ## Show this message
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)