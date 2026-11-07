// E1.5.1: deterministic received power and SNR, 5G-LENA vs the Python link budget.
//
// Project-owned program (cmwave). Source of truth: <repo>/ns3/scratch/cmwave/;
// installed into <ns-3>/scratch/cmwave/ by scripts/e1_5_install_ns3_program.sh,
// which checks the SHA-256 of the installed copy. No NR or ns-3 file is modified.
//
// One gNB, K UEs on a single band and BWP. Each gNB-UE pair has a PRESCRIBED
// loss (MatrixPropagationLossModel, symmetric): no ns-3 path-loss model, no
// shadowing, no fast fading, no building loss. Isotropic 1x1 antennas.
// The spectrum channel is created with NrChannelHelper::CreateChannel(0)
// (no propagation and no fading model; NR's CSI-RS filter only) and attached
// with BandwidthPartInfo::SetChannel, as in NR test/ul-scheduling-test.cc.
//
// Outputs (in the current working directory):
//   e15_dl_tb.csv       one row per DL data TB received by a UE (RxPacketTraceUe)
//   e15_ul_tb.csv       one row per UL data TB received by the gNB (RxPacketTraceGnb)
//   e15_dl_rxpower.csv  per UE: min/max received power per RB of DL data (RxDataTrace)
//   e15_rnti_map.csv    ue_index, imsi, rnti (after attach)
//   e15_run_info.csv    parameters actually applied (incl. RB counts read back)

#include "ns3/antenna-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/nr-module.h"
#include "ns3/propagation-module.h"
#include "ns3/spectrum-module.h"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("CmwaveE15LinkValidation");

static std::ofstream g_dlTb;
static std::ofstream g_ulTb;

struct RbPowerStats
{
    uint64_t events{0};
    double minDbm{std::numeric_limits<double>::infinity()};
    double maxDbm{-std::numeric_limits<double>::infinity()};
};

static std::map<uint32_t, RbPowerStats> g_rbPower;

static void
WriteTb(std::ofstream& out, int64_t ueIndex, const RxPacketTraceParams& p)
{
    out << ueIndex << ',' << p.m_rnti << ',' << Simulator::Now().GetNanoSeconds() << ','
        << std::setprecision(17) << p.m_sinr << ',' << p.m_sinrMin << ',' << p.m_rbAssignedNum
        << ',' << static_cast<uint32_t>(p.m_mcs) << ',' << p.m_tbSize << ','
        << static_cast<uint32_t>(p.m_rank) << ',' << static_cast<uint32_t>(p.m_rv) << ','
        << static_cast<uint32_t>(p.m_numSym) << ',' << p.m_corrupt << ',' << p.m_tbler << '\n';
}

static void
DlTbTrace(uint32_t ueIndex, RxPacketTraceParams p)
{
    WriteTb(g_dlTb, ueIndex, p);
}

static void
UlTbTrace(RxPacketTraceParams p)
{
    WriteTb(g_ulTb, -1, p); // UE identified later through e15_rnti_map.csv
}

static void
DlRxDataTrace(uint32_t ueIndex,
              const SfnSf& /* sfn */,
              Ptr<const SpectrumValue> v,
              const Time& /* t */,
              uint16_t /* bwpId */,
              uint16_t /* cellId */)
{
    auto& s = g_rbPower[ueIndex];
    auto band = v->ConstBandsBegin();
    for (auto it = v->ConstValuesBegin(); it != v->ConstValuesEnd(); ++it, ++band)
    {
        if (*it <= 0.0)
        {
            continue; // RB not used by this transmission
        }
        double wPerRb = (*it) * (band->fh - band->fl);
        double dbm = 10.0 * std::log10(wPerRb) + 30.0;
        s.minDbm = std::min(s.minDbm, dbm);
        s.maxDbm = std::max(s.maxDbm, dbm);
        s.events++;
    }
}

static void
WriteRntiMap(NetDeviceContainer ueDevs, std::string path)
{
    std::ofstream out(path);
    out << "ue_index,imsi,rnti\n";
    for (uint32_t i = 0; i < ueDevs.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueDevs.Get(i));
        out << i << ',' << dev->GetImsi() << ',' << dev->GetRrc()->GetRnti() << '\n';
    }
}

// Amendment 1: RNTIs can change after attach, so every RRC connection
// establishment is logged with its time; UL TBs are attributed by
// (rnti, time) to the IMSI of the latest establishment of that RNTI.
static std::ofstream g_rrc;

static void
ConnectionEstablished(uint64_t imsi, uint16_t cellId, uint16_t rnti)
{
    g_rrc << Simulator::Now().GetNanoSeconds() << ',' << imsi << ',' << cellId << ',' << rnti << '\n';
}

// Amendment 1: the UE learns the cell bandwidth only after attach, so its RB
// count is read when the applications start, not at configuration time.
static void
WriteRbAfterAttach(NetDeviceContainer ueDevs, std::string path)
{
    std::ofstream out(path);
    out << "ue_index,imsi,rnti,ue_rb_num\n";
    for (uint32_t i = 0; i < ueDevs.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueDevs.Get(i));
        out << i << ',' << dev->GetImsi() << ',' << dev->GetRrc()->GetRnti() << ','
            << NrHelper::GetUePhy(dev, 0)->GetRbNum() << '\n';
    }
}

int
main(int argc, char* argv[])
{
    double frequency = 3.5e9;
    double bandwidth = 20e6;
    uint16_t numerology = 1;
    double rbOverhead = 0.073;
    double gnbTxPower = 44.0;
    double ueTxPower = 23.0;
    double nfGnb = 5.0;
    double nfUe = 7.0;
    std::string lossFile;
    std::string scheduler = "ns3::NrMacSchedulerTdmaRR";
    double dlRateBps = 20e6;
    double ulRateBps = 2e6;
    uint32_t packetSize = 1000;
    uint32_t ulPacketSize = 0; // amendment 2: 0 = same as packetSize
    double appStart = 0.4;
    double simTime = 1.0;
    std::string tddPattern = "DL|DL|DL|F|UL|DL|DL|DL|F|UL|"; // amendment 1 (DDDSU)
    bool enableRlf = false;                                  // amendment 1

    CommandLine cmd(__FILE__);
    cmd.AddValue("frequency", "carrier frequency [Hz]", frequency);
    cmd.AddValue("bandwidth", "channel bandwidth [Hz]", bandwidth);
    cmd.AddValue("numerology", "NR numerology", numerology);
    cmd.AddValue("rbOverhead", "NrHelper RbOverhead (sets the RB count)", rbOverhead);
    cmd.AddValue("gnbTxPower", "gNB total transmit power [dBm]", gnbTxPower);
    cmd.AddValue("ueTxPower", "UE transmit power [dBm]", ueTxPower);
    cmd.AddValue("nfGnb", "gNB noise figure [dB]", nfGnb);
    cmd.AddValue("nfUe", "UE noise figure [dB]", nfUe);
    cmd.AddValue("lossFile", "CSV with ue_index,loss_db (one UE per row)", lossFile);
    cmd.AddValue("scheduler", "MAC scheduler TypeId", scheduler);
    cmd.AddValue("dlRate", "DL offered load per UE [bit/s]", dlRateBps);
    cmd.AddValue("ulRate", "UL offered load per UE [bit/s]", ulRateBps);
    cmd.AddValue("packetSize", "UDP payload [bytes] (DL, and UL unless ulPacketSize is set)", packetSize);
    cmd.AddValue("ulPacketSize", "UL UDP payload [bytes]; 0 = packetSize", ulPacketSize);
    cmd.AddValue("appStart", "application start [s]", appStart);
    cmd.AddValue("simTime", "simulation end [s]", simTime);
    cmd.AddValue("tddPattern", "gNB TDD slot pattern", tddPattern);
    cmd.AddValue("enableRlf", "UE radio-link-failure detection", enableRlf);
    cmd.Parse(argc, argv);
    NS_ABORT_MSG_IF(lossFile.empty(), "--lossFile is required");

    std::vector<double> losses;
    {
        std::ifstream in(lossFile);
        NS_ABORT_MSG_IF(!in, "cannot open " << lossFile);
        std::string line;
        std::getline(in, line); // header
        while (std::getline(in, line))
        {
            if (line.empty())
            {
                continue;
            }
            std::stringstream ss(line);
            std::string idx;
            std::string loss;
            std::getline(ss, idx, ',');
            std::getline(ss, loss, ',');
            NS_ABORT_MSG_IF(std::stoul(idx) != losses.size(), "ue_index must be 0..K-1 in order");
            losses.push_back(std::stod(loss));
        }
    }
    const uint32_t nUe = losses.size();
    if (ulPacketSize == 0)
    {
        ulPacketSize = packetSize;
    }
    NS_ABORT_MSG_IF(nUe == 0, "no UEs in " << lossFile);

    Config::SetDefault("ns3::NrRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    NodeContainer gnbNodes;
    gnbNodes.Create(1);
    NodeContainer ueNodes;
    ueNodes.Create(nUe);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(gnbNodes);
    mobility.Install(ueNodes);
    gnbNodes.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0.0, 0.0, 27.0));
    for (uint32_t i = 0; i < nUe; ++i)
    {
        // Positions do not affect the prescribed losses; they only keep nodes distinct.
        ueNodes.Get(i)->GetObject<MobilityModel>()->SetPosition(Vector(20.0 + 10.0 * i, 0.0, 1.5));
    }

    auto lossModel = CreateObject<MatrixPropagationLossModel>();
    lossModel->SetDefaultLoss(400.0); // UE-UE pairs
    for (uint32_t i = 0; i < nUe; ++i)
    {
        lossModel->SetLoss(gnbNodes.Get(0)->GetObject<MobilityModel>(),
                           ueNodes.Get(i)->GetObject<MobilityModel>(),
                           losses[i],
                           true);
    }

    CcBwpCreator ccBwpCreator;
    CcBwpCreator::SimpleOperationBandConf bandConf(frequency, bandwidth, 1);
    OperationBandInfo band = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);
    auto channelHelper = CreateObject<NrChannelHelper>();
    Ptr<SpectrumChannel> channel = channelHelper->CreateChannel(0);
    channel->AddPropagationLossModel(lossModel);
    NS_ABORT_MSG_IF(channel->GetPhasedArraySpectrumPropagationLossModel() ||
                        channel->GetSpectrumPropagationLossModel(),
                    "stage 1 requires a channel without fading");
    band.GetBwpAt(0, 0)->SetChannel(channel);
    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({band});

    auto epcHelper = CreateObject<NrPointToPointEpcHelper>();
    auto bfHelper = CreateObject<IdealBeamformingHelper>();
    bfHelper->SetAttribute("BeamformingMethod", TypeIdValue(DirectPathBeamforming::GetTypeId()));
    auto nrHelper = CreateObject<NrHelper>();
    nrHelper->SetBeamformingHelper(bfHelper);
    nrHelper->SetEpcHelper(epcHelper);
    nrHelper->SetAttribute("RbOverhead", DoubleValue(rbOverhead));
    nrHelper->SetSchedulerTypeId(TypeId::LookupByName(scheduler));
    nrHelper->SetGnbPhyAttribute("Numerology", UintegerValue(numerology));
    nrHelper->SetGnbPhyAttribute("Pattern", StringValue(tddPattern));
    nrHelper->SetUePhyAttribute("EnableRlfDetection", BooleanValue(enableRlf));
    nrHelper->SetGnbPhyAttribute("TxPower", DoubleValue(gnbTxPower));
    nrHelper->SetGnbPhyAttribute("NoiseFigure", DoubleValue(nfGnb));
    nrHelper->SetGnbPhyAttribute(
        "PowerAllocationType",
        EnumValue(NrSpectrumValueHelper::UNIFORM_POWER_ALLOCATION_BW));
    nrHelper->SetUePhyAttribute("TxPower", DoubleValue(ueTxPower));
    nrHelper->SetUePhyAttribute("NoiseFigure", DoubleValue(nfUe));
    nrHelper->SetUePhyAttribute("EnableUplinkPowerControl", BooleanValue(false));
    nrHelper->SetUePhyAttribute(
        "PowerAllocationType",
        EnumValue(NrSpectrumValueHelper::UNIFORM_POWER_ALLOCATION_USED));
    for (const char* who : {"Ue", "Gnb"})
    {
        auto set = [&](const std::string& n, const AttributeValue& v) {
            if (std::string(who) == "Ue")
            {
                nrHelper->SetUeAntennaAttribute(n, v);
            }
            else
            {
                nrHelper->SetGnbAntennaAttribute(n, v);
            }
        };
        set("NumRows", UintegerValue(1));
        set("NumColumns", UintegerValue(1));
        set("AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));
    }

    NetDeviceContainer gnbDevs = nrHelper->InstallGnbDevice(gnbNodes, allBwps);
    NetDeviceContainer ueDevs = nrHelper->InstallUeDevice(ueNodes, allBwps);
    nrHelper->AssignStreams({.gnbDevs = gnbDevs, .ueDevs = ueDevs});

    auto [remoteHost, remoteHostAddr] = epcHelper->SetupRemoteHost("100Gb/s", 2500, Seconds(0.0));
    InternetStackHelper internet;
    internet.Install(ueNodes);
    Ipv4InterfaceContainer ueIfaces = epcHelper->AssignUeIpv4Address(ueDevs);
    nrHelper->AttachToClosestGnb(ueDevs, gnbDevs);

    // Traffic: DL CBR remote host -> each UE; UL CBR each UE -> remote host.
    const uint16_t dlPort = 1234;
    const uint16_t ulPort = 2345;
    ApplicationContainer servers;
    ApplicationContainer clients;
    servers.Add(UdpServerHelper(dlPort).Install(ueNodes));
    servers.Add(UdpServerHelper(ulPort).Install(remoteHost));
    for (uint32_t i = 0; i < nUe; ++i)
    {
        UdpClientHelper dl(ueIfaces.GetAddress(i), dlPort);
        dl.SetAttribute("MaxPackets", UintegerValue(0xFFFFFFFF));
        dl.SetAttribute("PacketSize", UintegerValue(packetSize));
        dl.SetAttribute("Interval", TimeValue(Seconds(packetSize * 8.0 / dlRateBps)));
        clients.Add(dl.Install(remoteHost));
        UdpClientHelper ul(remoteHostAddr, ulPort);
        ul.SetAttribute("MaxPackets", UintegerValue(0xFFFFFFFF));
        ul.SetAttribute("PacketSize", UintegerValue(ulPacketSize));
        ul.SetAttribute("Interval", TimeValue(Seconds(ulPacketSize * 8.0 / ulRateBps)));
        clients.Add(ul.Install(ueNodes.Get(i)));
    }
    servers.Start(Seconds(appStart));
    clients.Start(Seconds(appStart));
    servers.Stop(Seconds(simTime));
    clients.Stop(Seconds(simTime));

    // Traces
    g_dlTb.open("e15_dl_tb.csv");
    g_ulTb.open("e15_ul_tb.csv");
    const char* header =
        "ue_index,rnti,time_ns,sinr_lin,sinr_min_lin,rb_assigned,mcs,tb_size,rank,rv,num_sym,"
        "corrupt,tbler\n";
    g_dlTb << header;
    g_ulTb << header;
    for (uint32_t i = 0; i < nUe; ++i)
    {
        auto sp = NrHelper::GetUePhy(ueDevs.Get(i), 0)->GetSpectrumPhy();
        sp->TraceConnectWithoutContext("RxPacketTraceUe", MakeBoundCallback(&DlTbTrace, i));
        sp->TraceConnectWithoutContext("RxDataTrace", MakeBoundCallback(&DlRxDataTrace, i));
    }
    NrHelper::GetGnbPhy(gnbDevs.Get(0), 0)
        ->GetSpectrumPhy()
        ->TraceConnectWithoutContext("RxPacketTraceGnb", MakeCallback(&UlTbTrace));
    Simulator::Schedule(Seconds(appStart - 0.05), &WriteRntiMap, ueDevs, std::string("e15_rnti_map.csv"));
    g_rrc.open("e15_rrc_events.csv");
    g_rrc << "time_ns,imsi,cell_id,rnti\n";
    DynamicCast<NrGnbNetDevice>(gnbDevs.Get(0))
        ->GetRrc()
        ->TraceConnectWithoutContext("ConnectionEstablished", MakeCallback(&ConnectionEstablished));
    Simulator::Schedule(Seconds(appStart),
                        &WriteRbAfterAttach,
                        ueDevs,
                        std::string("e15_rb_after_attach.csv"));

    {
        auto gp = NrHelper::GetGnbPhy(gnbDevs.Get(0), 0);
        auto up = NrHelper::GetUePhy(ueDevs.Get(0), 0);
        std::ofstream info("e15_run_info.csv");
        info << "key,value\n"
             << "frequency_hz," << std::setprecision(17) << frequency << '\n'
             << "bandwidth_hz," << bandwidth << '\n'
             << "numerology," << numerology << '\n'
             << "rb_overhead," << rbOverhead << '\n'
             << "gnb_rb_num," << gp->GetRbNum() << '\n'
             << "ue_rb_num," << up->GetRbNum() << '\n'
             << "gnb_tx_power_dbm," << gp->GetTxPower() << '\n'
             << "gnb_noise_figure_db," << gp->GetNoiseFigure() << '\n'
             << "ue_noise_figure_db," << up->GetNoiseFigure() << '\n'
             << "scheduler," << scheduler << '\n'
             << "tdd_pattern," << gp->GetPattern() << '\n'
             << "dl_rate_bps," << dlRateBps << '\n'
             << "ul_rate_bps," << ulRateBps << '\n'
             << "dl_packet_size," << packetSize << '\n'
             << "ul_packet_size," << ulPacketSize << '\n'
             << "enable_rlf," << enableRlf << '\n'
             << "n_ue," << nUe << '\n'
             << "app_start_s," << appStart << '\n'
             << "sim_time_s," << simTime << '\n';
        DoubleValue ueP;
        up->GetAttribute("TxPower", ueP);
        info << "ue_tx_power_dbm," << ueP.Get() << '\n';
    }

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    std::ofstream rp("e15_dl_rxpower.csv");
    rp << "ue_index,n_rb_samples,min_rb_power_dbm,max_rb_power_dbm\n";
    for (uint32_t i = 0; i < nUe; ++i)
    {
        auto it = g_rbPower.find(i);
        if (it == g_rbPower.end())
        {
            rp << i << ",0,,\n";
        }
        else
        {
            rp << i << ',' << it->second.events << ',' << std::setprecision(17) << it->second.minDbm
               << ',' << it->second.maxDbm << '\n';
        }
    }
    g_dlTb.close();
    g_ulTb.close();
    g_rrc.close();
    Simulator::Destroy();
    return 0;
}
