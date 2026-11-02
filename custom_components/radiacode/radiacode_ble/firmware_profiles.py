"""Record declarations extracted from CRC-verified vendor 1XX firmware.

See docs/firmware-415-beta-2026-11-01.md for provenance and limits. These are
record declarations only, never device settings or calibration. Selection uses
the existing GET_VERSION target build and serial model, never packet length.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FirmwareProfile:
    model: str
    version: str
    build: str
    container_sha256: str
    configuration_sha256: str
    layout_sha256: str

    @property
    def name(self) -> str:
        return f"{self.model}-{self.version}-{self.build}"


PROFILES = (
    FirmwareProfile(
        model='RC-101',
        version='4.15',
        build='Oct 15 2026 10:17:27',
        container_sha256='26c58f69a18998db1ced08d5f4fa1069418daac1c55aacbffc9f9e92aaa21ef4',
        configuration_sha256='ad04354ce287eafdef88d0455fe97facc229804f51bc5b96e596ddfc996489e8',
        layout_sha256='93b0d82d0e457ba7c96961d2e62d717593efd656f9d4d5166c61fae380b8d834',
    ),
    FirmwareProfile(
        model='RC-102',
        version='4.15',
        build='Oct 15 2026 10:17:49',
        container_sha256='474d0326011dfdec7a7111c985c7bde9ebc5772acf665fb1be9be4414c20859e',
        configuration_sha256='01688ca1008c2885d2159c202a7e117c0d3b34de76b452f02d0d249d4bd5b931',
        layout_sha256='93b0d82d0e457ba7c96961d2e62d717593efd656f9d4d5166c61fae380b8d834',
    ),
    FirmwareProfile(
        model='RC-103',
        version='4.15',
        build='Oct 15 2026 10:18:10',
        container_sha256='d7e7562301703fef7c268f5db52e8a6c0f42c8e284d2394d9fe5e649689fde39',
        configuration_sha256='d891be85f9463eeef02223858fa816acbb5a8fa8e38684d3b77234c8be18563b',
        layout_sha256='93b0d82d0e457ba7c96961d2e62d717593efd656f9d4d5166c61fae380b8d834',
    ),
    FirmwareProfile(
        model='RC-103G',
        version='4.15',
        build='Oct 15 2026 10:18:33',
        container_sha256='42cb4b33ecdecaeaca1dd89f26e62302007fb21a62564e734c4416632bba5a97',
        configuration_sha256='de436b0f800aff4b6efefee4baf43818c9ee1412b8138abe14e60ec93d1b7960',
        layout_sha256='93b0d82d0e457ba7c96961d2e62d717593efd656f9d4d5166c61fae380b8d834',
    ),
    FirmwareProfile(
        model='RC-110',
        version='4.15',
        build='Oct 15 2026 10:18:57',
        container_sha256='707a530ef087a6342d01b3f0cf6b04776b98d90f05dce1c8de0f9dbf32ff5ffc',
        configuration_sha256='60e859527f072d9c1fa563ee28579df3b3bc6e4bcb1d31824e3d0f90b0482188',
        layout_sha256='93b0d82d0e457ba7c96961d2e62d717593efd656f9d4d5166c61fae380b8d834',
    ),
)

# All five vendor builds declare these same record types, units and scales.
RECORD_CONFIGURATION = """[DataStructure]
[[GRP_RealTimeData]]
Id=0
[[[CHN_CountRate]]]
Id=3
Unit=cps
DType=1
RType=1
[[[CHN_DoseRate]]]
Id=4
Unit=rem/h
DType=1
RType=1
[[[CHN_CountRateErr]]]
Id=16
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_DoseRateErr]]]
Id=5
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[[CHN_RealTimeFlags]]]
Id=17
DType=4
RType=8
[[GRP_RawData]]
Id=1
[[[CHN_CountRate]]]
Id=3
Unit=cps
DType=1
RType=1
[[[CHN_DoseRate]]]
Id=4
Unit=rem/h
DType=1
RType=1
[[GRP_DoseRateDB]]
Id=2
[[[CHN_Count]]]
Id=2
DType=8
RType=8
[[[CHN_CountRate]]]
Id=3
Unit=cps
DType=1
RType=1
[[[CHN_DoseRate]]]
Id=4
Unit=rem/h
DType=1
RType=1
[[[CHN_DoseRateErr]]]
Id=5
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[GRP_MeasData]]
Id=5
[[[CHN_CountRate]]]
Id=3
Unit=cps
DType=1
RType=1
[[[CHN_DoseRate]]]
Id=4
Unit=rem/h
DType=1
RType=1
[[[CHN_CountRateErr]]]
Id=16
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_DoseRateErr]]]
Id=5
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_MeasTime]]]
Id=23
Unit=s
DType=4
RType=4
[[[CHN_MeasSID]]]
Id=24
DType=2
RType=2
[[[CHN_MeasCount]]]
Id=25
DType=2
RType=2
[[[CHN_MeasFlags]]]
Id=26
DType=2
RType=2
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[GRP_RareData]]
Id=3
[[[CHN_Duration]]]
Id=6
DType=8
RType=8
Unit=s
[[[CHN_Dose]]]
Id=7
Unit=rem
DType=1
RType=1
[[[CHN_Temperature]]]
Id=8
Unit=°C
DType=5
RType=1
Expr=1
P1=0.01
P2=-20
[[[CHN_ChargeLevel]]]
Id=9
Unit=%
DType=4
RType=1
Expr=1
P1=0.01
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[GRP_DoseCounter]]
Id=4
[[[CHN_DoseCounter]]]
Id=22
Unit=urem
DType=8
RType=8
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[GRP_Event]]
Id=7
[[[CHN_Event]]]
Id=14
DType=2
RType=2
[[[CHN_EventChnMsk]]]
Id=15
DType=2
RType=8
Expr=1
P1=8
P2=7
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
[[[CHN_CountRate]]]
Id=3
Unit=cps
DType=1
RType=1
[[[CHN_CountRateErr]]]
Id=16
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_DoseRate]]]
Id=4
Unit=rem/h
DType=1
RType=1
[[[CHN_DoseRateErr]]]
Id=5
Unit=%
DType=4
RType=1
Expr=1
P1=0.1
[[[CHN_Duration]]]
Id=6
DType=8
RType=8
Unit=s
[[[CHN_Dose]]]
Id=7
Unit=rem
DType=1
RType=1
[[[CHN_Temperature]]]
Id=8
Unit=°C
DType=5
RType=1
Expr=1
P1=0.01
P2=-20
[[[CHN_ChargeLevel]]]
Id=9
Unit=%
DType=4
RType=1
Expr=1
P1=0.01
"""
