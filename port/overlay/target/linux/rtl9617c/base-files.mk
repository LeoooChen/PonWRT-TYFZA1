# SPDX-License-Identifier: GPL-2.0-only
# A53 userspace is compatible with A55; kernel packages stay board-specific.
define FeedSourcesAppendAPK
	( \
		echo '# Official A53 userspace feeds; do not add another target kernel feed.'; \
		echo '# Add custom feeds to /etc/apk/repositories.d/customfeeds.list'; \
		$(foreach feed,base luci packages routing telephony video,echo 'https://downloads.openwrt.org/snapshots/packages/aarch64_cortex-a53/$(feed)/packages.adb';) \
	) > $(1)
endef
