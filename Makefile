VERSION := 0.1
REPOSITORY := localhost
IMAGE := md-text-base_fs
TAG := $(REPOSITORY)/messydesk/$(IMAGE):$(VERSION)

IMAGES := $(shell docker images -f "dangling=true" -q)
CONTAINERS := $(shell docker ps -a -q -f status=exited)

ifneq (,$(wildcard .env))
    include .env
    export
endif

ifeq ($(MD_PATH),)
    $(error MD_PATH is not set. Please set it in .env file or environment)
endif

clean:
	docker rm -f $(CONTAINERS)
	docker rmi -f $(IMAGES)

build:
	docker build -t $(REPOSITORY)/messydesk/$(IMAGE):$(VERSION) .

start:
	docker run -d --name $(IMAGE) \
		-p 9008:9008 \
		-v $(MD_PATH)/data/:/app/data:Z \
		-e MD_PATH=/app \
		-e MD_URL=http://host.containers.internal:8200 \
		--restart unless-stopped \
		$(REPOSITORY)/messydesk/$(IMAGE):$(VERSION)

start_interactive:
	docker run -it --name $(IMAGE) \
		-p 9008:9008 \
		-v $(MD_PATH)/data/:/app/data:Z \
		-e MD_PATH=/app \
		-e MD_URL=http://host.containers.internal:8200 \
		$(REPOSITORY)/messydesk/$(IMAGE):$(VERSION) \
		bash

restart:
	docker stop $(IMAGE)
	docker rm $(IMAGE)
	$(MAKE) start

bash:
	docker exec -it $(IMAGE) bash

