from app import about

CPUINFO = """processor\t: 0
model name\t: AMD Ryzen 7 5800H   with Radeon Graphics
physical id\t: 0
core id\t\t: 0

processor\t: 1
model name\t: AMD Ryzen 7 5800H   with Radeon Graphics
physical id\t: 0
core id\t\t: 0

processor\t: 2
model name\t: AMD Ryzen 7 5800H   with Radeon Graphics
physical id\t: 0
core id\t\t: 1
"""


def test_cpu_info_parses_name_and_physical_cores():
    c = about.cpu_info(CPUINFO)
    assert c["name"] == "AMD Ryzen 7 5800H with Radeon Graphics" and c["cores"] == 2 and c["threads"]


def test_cpu_info_empty_is_none():
    assert about.cpu_info("")["name"] is None and about.cpu_info("")["cores"] is None


def test_os_name():
    assert about.os_name('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.2 LTS"\n') == "Ubuntu 24.04.2 LTS"


def test_lspci_gpus():
    t = '00:02.0 "VGA compatible controller" "Intel Corporation" "UHD Graphics 630" -r02 "Dell" "x"\n00:1f.3 "Audio device" "Intel" "HDA"\n'
    assert about.lspci_gpus(t) == ["Intel Corporation UHD Graphics 630"]


def test_about_endpoint_shape(client):
    j = client.get("/api/about").json()
    assert j["name"] == "ZEUS" and j["version"] == "0.1.0" and j["founder"] and j["github"].startswith("https://github.com/")
    assert set(j) >= {"platform", "cpu", "gpus", "memory"} and isinstance(j["gpus"], list)
    assert j["memory"]["total"] is None or j["memory"]["total"] > 0
