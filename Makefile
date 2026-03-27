IMAGES := $(shell docker images -f "dangling=true" -q)
CONTAINERS := $(shell docker ps -a -q -f status=exited)
VERSION := 0.1
REPOSITORY := local
IMAGE := md-base


clean:
	docker rm -f $(CONTAINERS)
	docker rmi -f $(IMAGES)

build:
	docker build -t $(REPOSITORY)/messydesk/$(IMAGE):$(VERSION) .

start:
	docker run -d --name $(IMAGE) \
		-p 9008:9008 \
		-e MD_URL=http://host.containers.internal:8200 \
		--restart unless-stopped \
		$(REPOSITORY)/messydesk/$(IMAGE):$(VERSION)

restart:
	docker stop $(IMAGE)
	docker rm $(IMAGE)
	$(MAKE) start

bash:
	docker exec -it $(IMAGE) bash

