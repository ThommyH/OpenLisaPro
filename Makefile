DOCKER ?= docker
REGISTRY ?= 192.168.178.69:5555
IMAGE_NAME ?= open-lisa-pro
IMAGE = $(REGISTRY)/$(IMAGE_NAME)
VERSION ?= $(shell sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)

.PHONY: build docker-build publish docker-publish

build: docker-build

docker-build:
	$(DOCKER) build --build-arg APP_VERSION=$(VERSION) -t $(IMAGE):$(VERSION) -t $(IMAGE):latest -f Dockerfile .

# The local registry speaks plain HTTP. BuildKit gets an isolated registry
# configuration from scripts/publish-image.sh; Docker daemon settings stay untouched.
publish: docker-publish

docker-publish:
	DOCKER="$(DOCKER)" REGISTRY="$(REGISTRY)" IMAGE_NAME="$(IMAGE_NAME)" VERSION="$(VERSION)" ./scripts/publish-image.sh
