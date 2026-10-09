from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text().replace('.name = "rtl_ax_ap",', '.name = "rtl_ax_ap_live",')
helper = r'''
static void rtl_ax_observe_stage(struct rtl_ax_ap *radio, const char *stage)
{
	struct rtl_ax_rx_pipeline snapshot;
	char host[1536];
	unsigned int point;

	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	rtl_ax_rx_open(radio->rx);
	for (point = 0; point < 2; point++) {
		unsigned int i;

		if (point)
			msleep(5000);
		rtl_ax_rx_pipeline_read(&radio->io, &snapshot);
		dev_info(&radio->pdev->dev,
			"RXstage=%s point=%u CCA=%u,%u HAXI=%08x DMAC=%08x CMAC=%08x RXIDX=%08x,%08x\n",
			stage, point, snapshot.cca[0], snapshot.cca[1], snapshot.haxi,
			snapshot.dmac, snapshot.cmac, snapshot.rx_index[0], snapshot.rx_index[1]);
		dev_info(&radio->pdev->dev, "RXstage=%s point=%u CCKgate=%08x,%08x,%08x\n",
			stage, point, snapshot.cck_gate[0], snapshot.cck_gate[1], snapshot.cck_gate[2]);
		for (i = 0; i < 5; i++)
			dev_info(&radio->pdev->dev, "RXstage=%s point=%u PHY=%u OK=%u error=%u\n",
				stage, point, i, snapshot.crc_ok[i], snapshot.crc_error[i]);
		for (i = 0; i < 8; i++)
			dev_info(&radio->pdev->dev, "RXstage=%s point=%u MAC=%u OK=%u error=%u FA=%u\n",
				stage, point, i, snapshot.mac_crc_ok[i], snapshot.mac_crc_error[i],
				snapshot.mac_false_alarm[i]);
	}
	rtl_ax_rx_close(radio->rx);
	rtl_ax_hci_pause(&radio->io);
	pci_clear_master(radio->pdev);
	rtl_ax_rx_status(radio->rx, host, sizeof(host));
	dev_info(&radio->pdev->dev, "RXstage=%s host:\n%s", stage, host);
}

'''
anchor = 'static int rtl_ax_load_firmware(struct rtl_ax_ap *radio, const struct firmware *fw)'
assert s.count(anchor) == 1
s = s.replace(anchor, helper + anchor)
for anchor, stage in [
    ('\trtl_ax_hal_start_receive(&radio->io, hw_rts_time, hw_rts_length);', 'MAC'),
    ('\tret = rtl_ax_bb_dm_ic_start(&radio->io, radio->chip, &radio->bb_dm);', 'base-FEM'),
    ('\trtl_ax_cfo_initialize(&radio->io, radio->chip, radio->flash_calibration.xtal, &radio->cfo);', 'DIG-CFO'),
    ('\trtl_ax_environment_initialize(&radio->io, radio->chip, &radio->environment);', 'environment'),
]:
    assert s.count(anchor) == 1
    call = f'\trtl_ax_observe_stage(radio, "{stage}");'
    s = s.replace(anchor, call + '\n' + anchor if stage == 'base-FEM' else anchor + '\n' + call)
p.write_text(s)
