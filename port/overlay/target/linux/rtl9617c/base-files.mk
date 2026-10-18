# SPDX-License-Identifier: GPL-2.0-only
# Release assets form one flat repository for target and userspace packages.
define FeedSourcesAppendAPK
	( \
		echo '# This repository belongs to this firmware build; sysupgrade replaces it.'; \
		echo '# Add custom feeds to /etc/apk/repositories.d/customfeeds.list'; \
		echo '%U/packages.adb'; \
	) > $(1)
endef
