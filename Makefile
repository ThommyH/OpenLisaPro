DOCKER ?= docker
REGISTRY ?= 192.168.178.69:5555
IMAGE_NAME ?= open-lisa-pro
IMAGE = $(REGISTRY)/$(IMAGE_NAME)
VERSION ?= $(shell sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)
APP_GIT_COMMIT ?= $(shell git rev-parse --short=7 HEAD 2>/dev/null || printf unknown)

.PHONY: build docker-build

build: docker-build

docker-build:
	$(DOCKER) build --build-arg APP_GIT_COMMIT=$(APP_GIT_COMMIT) -t $(IMAGE):$(VERSION) -t $(IMAGE):latest -f Dockerfile .
