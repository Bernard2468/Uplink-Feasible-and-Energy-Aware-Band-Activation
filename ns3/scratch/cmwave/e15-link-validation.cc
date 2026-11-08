// E1.5 validation program (cmwave). Modes:
//   --mode=link     (default) E1.5.1: deterministic received power and SNR,
//                   5G-LENA vs the Python link budget. Described below.
//   --mode=chan     E1.5.2: gNB array gain in the TR 38.901 channel, channel level
//                   (see RunChannelMode).
//   --mode=nrfading E1.5.2: NR stack with TR 38.901 fast fading and a gNB array
//                   (see RunNrFadingMode). E1.5.2-1 adds --gnbBeam=cb|eig.
//   --mode=beams    E1.5.2-1: DirectPath, DFT codebook and eigen-beam gains on the
//                   same stored channel realization (see RunBeamsMode).
//
// Mode "link" (E1.5.1):
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

#include "ns3/three-gpp-channel-model.h"
#include "ns3/three-gpp-spectrum-propagation-loss-model.h"

#include <cmath>
#include <complex>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <sys/resource.h>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("CmwaveE15LinkValidation");

// ---------------------------------------------------------------------------
// E1.5.2 additions (modes "chan" and "nrfading"). Mode "link" (default) is the
// E1.5.1 program and is unchanged.
//
// Links file (both new modes): CSV header
//   link_index,name,x,y,z,los,o2i,bearing_deg,loss_db
// One row per gNB-UE link, link_index 0..K-1 in order; los/o2i are 0/1.
// ---------------------------------------------------------------------------

struct LinkRow
{
    std::string name;
    Vector pos;
    bool los{false};
    bool o2i{false};
    double bearingDeg{0.0};
    double lossDb{0.0};
};

static std::vector<LinkRow>
ReadLinks(const std::string& path)
{
    std::vector<LinkRow> out;
    std::ifstream in(path);
    NS_ABORT_MSG_IF(!in, "cannot open " << path);
    std::string line;
    std::getline(in, line);
    NS_ABORT_MSG_IF(line != "link_index,name,x,y,z,los,o2i,bearing_deg,loss_db",
                    "unexpected links header: " << line);
    while (std::getline(in, line))
    {
        if (line.empty())
        {
            continue;
        }
        std::stringstream ss(line);
        std::vector<std::string> f;
        std::string tok;
        while (std::getline(ss, tok, ','))
        {
            f.push_back(tok);
        }
        NS_ABORT_MSG_IF(f.size() != 9, "bad links row: " << line);
        NS_ABORT_MSG_IF(std::stoul(f[0]) != out.size(), "link_index must be 0..K-1 in order");
        LinkRow r;
        r.name = f[1];
        r.pos = Vector(std::stod(f[2]), std::stod(f[3]), std::stod(f[4]));
        r.los = std::stoi(f[5]) != 0;
        r.o2i = std::stoi(f[6]) != 0;
        r.bearingDeg = std::stod(f[7]);
        r.lossDb = std::stod(f[8]);
        out.push_back(r);
    }
    NS_ABORT_MSG_IF(out.empty(), "no links in " << path);
    return out;
}

/**
 * Channel condition prescribed per UE node (LOS/NLOS, O2O/O2I), as in E1.1/E1.2:
 * the condition is a property of the link geometry, not drawn at random.
 * The condition of a pair must not depend on the argument order (ns-3
 * regenerates the channel parameters when it changes). Pairs not involving the
 * gNB (UE-UE, prescribed loss 400 dB) get one fixed NLOS, O2O condition.
 */
class CmwavePrescribedConditionModel : public ChannelConditionModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::CmwavePrescribedConditionModel")
                                .SetParent<ChannelConditionModel>()
                                .SetGroupName("Propagation")
                                .AddConstructor<CmwavePrescribedConditionModel>();
        return tid;
    }

    void SetGnbNodeId(uint32_t id)
    {
        m_gnb = id;
    }

    void SetCondition(uint32_t ueNodeId, bool los, bool o2i)
    {
        m_cond[ueNodeId] =
            CreateObject<ChannelCondition>(los ? ChannelCondition::LOS : ChannelCondition::NLOS,
                                           o2i ? ChannelCondition::O2I : ChannelCondition::O2O);
    }

    Ptr<ChannelCondition> GetChannelCondition(Ptr<const MobilityModel> a,
                                              Ptr<const MobilityModel> b) const override
    {
        uint32_t ia = a->GetObject<Node>()->GetId();
        uint32_t ib = b->GetObject<Node>()->GetId();
        if (ia != m_gnb && ib != m_gnb)
        {
            return m_ueUe;
        }
        uint32_t ue = (ia == m_gnb) ? ib : ia;
        auto it = m_cond.find(ue);
        NS_ABORT_MSG_IF(it == m_cond.end(), "no prescribed condition for node " << ue);
        return it->second;
    }

    int64_t AssignStreams(int64_t /* stream */) override
    {
        return 0;
    }

  private:
    std::map<uint32_t, Ptr<ChannelCondition>> m_cond;
    uint32_t m_gnb{std::numeric_limits<uint32_t>::max()};
    Ptr<ChannelCondition> m_ueUe{
        CreateObject<ChannelCondition>(ChannelCondition::NLOS, ChannelCondition::O2O)};
};

NS_OBJECT_ENSURE_REGISTERED(CmwavePrescribedConditionModel);

// E1.5.2-0 amendment 1: UniformPlanarArray accepts BearingAngle only in
// [-pi, pi]; bearings are wrapped to [-180, 180) deg (270 deg -> -90 deg, the
// same orientation).
static double
BearingRad(double bearingDeg)
{
    double w = std::fmod(bearingDeg + 180.0, 360.0);
    if (w < 0)
    {
        w += 360.0;
    }
    return (w - 180.0) * M_PI / 180.0;
}

static Ptr<UniformPlanarArray>
MakeGnbArray(uint32_t rows, uint32_t cols, double bearingDeg, const std::string& element)
{
    auto upa = CreateObject<UniformPlanarArray>();
    upa->SetAttribute("NumRows", UintegerValue(rows));
    upa->SetAttribute("NumColumns", UintegerValue(cols));
    upa->SetAttribute("AntennaHorizontalSpacing", DoubleValue(0.5));
    upa->SetAttribute("AntennaVerticalSpacing", DoubleValue(0.5));
    upa->SetAttribute("BearingAngle", DoubleValue(BearingRad(bearingDeg)));
    upa->SetAttribute("DowntiltAngle", DoubleValue(0.0));
    if (element == "3gpp")
    {
        auto e = CreateObject<ThreeGppAntennaModel>();
        e->SetAttribute("RadiationPattern",
                        EnumValue<ThreeGppAntennaModel::RadiationPattern>(
                            ThreeGppAntennaModel::RadiationPattern::OUTDOOR));
        upa->SetAttribute("AntennaElement", PointerValue(e));
    }
    else
    {
        NS_ABORT_MSG_IF(element != "iso", "gnbElement must be 3gpp or iso");
        upa->SetAttribute("AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));
    }
    return upa;
}

static Ptr<UniformPlanarArray>
MakeIsoArray()
{
    auto upa = CreateObject<UniformPlanarArray>();
    upa->SetAttribute("NumRows", UintegerValue(1));
    upa->SetAttribute("NumColumns", UintegerValue(1));
    upa->SetAttribute("AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));
    return upa;
}

/**
 * Deterministic gain of a gNB array toward direction 'ang' (from the gNB) with
 * weights w, in the form the 3GPP channel uses for a ray leaving the gNB:
 * element power gain x |sum_k w_k exp(+j 2 pi r.d_k)|^2 when the gNB is the
 * channel's transmit side "s", and |sum_k conj(w_k) exp(+j 2 pi r.d_k)|^2 when
 * it is the receive side "u" (ns-3 three-gpp-channel-model.cc; long-term
 * component w_u^H H w_s, ns-3 commit 80c131f7f).
 */
static void
DeterministicGain(Ptr<const UniformPlanarArray> upa,
                  const PhasedArrayModel::ComplexVector& w,
                  const Angles& ang,
                  double* elemLin,
                  double* afS,
                  double* afU)
{
    auto [fPhi, fTheta] = upa->GetElementFieldPattern(ang, 0);
    *elemLin = fPhi * fPhi + fTheta * fTheta;
    const double si = std::sin(ang.GetInclination());
    const double ci = std::cos(ang.GetInclination());
    const double ca = std::cos(ang.GetAzimuth());
    const double sa = std::sin(ang.GetAzimuth());
    std::complex<double> s(0, 0);
    std::complex<double> u(0, 0);
    for (size_t k = 0; k < upa->GetNumElems(); ++k)
    {
        Vector d = upa->GetElementLocation(k);
        double ph = 2 * M_PI * (si * ca * d.x + si * sa * d.y + ci * d.z);
        std::complex<double> e = std::polar(1.0, ph);
        s += w[k] * e;
        u += std::conj(w[k]) * e;
    }
    *afS = std::norm(s);
    *afU = std::norm(u);
}

static Ptr<SpectrumSignalParameters>
FlatTxParams(uint32_t numRbs, double frequency, double scs)
{
    Ptr<const SpectrumModel> sm = NrSpectrumValueHelper::GetSpectrumModel(numRbs, frequency, scs);
    Ptr<SpectrumValue> psd = Create<SpectrumValue>(sm);
    (*psd) = 1.0;
    auto p = Create<SpectrumSignalParameters>();
    p->psd = psd;
    return p;
}

// ---------------------------------------------------------------------------
// E1.5.2-1 additions: per-RB channel vectors of the stored channel and beams.
// Complex products are written out in real arithmetic (no library calls).
// ---------------------------------------------------------------------------

using Cplx = std::complex<double>;

/**
 * Per-RB channel vector of the gNB array (role s of the stored matrix) toward a
 * one-element UE (role u), rebuilt from ns-3's stored channel:
 *   h_i[n] = sum_c H(0, n, c) exp(-j 2 pi f_i tau_c),   f_i = centre of RB i.
 * With the UE weight 1, ns-3's beamformed channel on RB i is g_i = sum_n w_n h_i[n]
 * (three-gpp-spectrum-propagation-loss-model.cc: CalcLongTerm with conj(uW) = 1,
 * GenSpectrumChannelMatrix; the Doppler factor is exactly 1 at t = 0, and stays 1 for
 * static nodes with the default vScatt = 0). Row-major: h[i * N + n].
 */
static std::vector<Cplx>
PerRbChannel(Ptr<const MatrixBasedChannelModel::ChannelMatrix> m,
             Ptr<const MatrixBasedChannelModel::ChannelParams> prm,
             Ptr<const SpectrumValue> psd)
{
    const size_t nU = m->m_channel.GetNumRows();
    const size_t nS = m->m_channel.GetNumCols();
    const size_t nC = m->m_channel.GetNumPages();
    NS_ABORT_MSG_IF(nU != 1, "PerRbChannel needs a one-element UE on the u side");
    NS_ABORT_MSG_IF(prm->m_delay.size() < nC, "fewer cluster delays than clusters");
    const size_t K = psd->GetValuesN();
    std::vector<Cplx> e(K * nC);
    size_t i = 0;
    for (auto b = psd->ConstBandsBegin(); b != psd->ConstBandsEnd(); ++b, ++i)
    {
        for (size_t c = 0; c < nC; ++c)
        {
            const double d = -2 * M_PI * b->fc * prm->m_delay[c];
            e[i * nC + c] = Cplx(std::cos(d), std::sin(d));
        }
    }
    std::vector<Cplx> h(K * nS);
    for (size_t k = 0; k < K; ++k)
    {
        for (size_t n = 0; n < nS; ++n)
        {
            double re = 0;
            double im = 0;
            for (size_t c = 0; c < nC; ++c)
            {
                const Cplx a = m->m_channel(0, n, c);
                const Cplx b = e[k * nC + c];
                re += a.real() * b.real() - a.imag() * b.imag();
                im += a.real() * b.imag() + a.imag() * b.real();
            }
            h[k * nS + n] = Cplx(re, im);
        }
    }
    return h;
}

/** (1/K) sum_i |h_i^T w|^2: mean over RBs of the beamformed channel power gain. */
static double
BeamGain(const std::vector<Cplx>& h, size_t K, size_t N, const PhasedArrayModel::ComplexVector& w)
{
    NS_ABORT_MSG_IF(w.GetSize() != N, "beam size mismatch");
    double acc = 0;
    for (size_t k = 0; k < K; ++k)
    {
        double re = 0;
        double im = 0;
        for (size_t n = 0; n < N; ++n)
        {
            const Cplx a = h[k * N + n];
            const Cplx b = w[n];
            re += a.real() * b.real() - a.imag() * b.imag();
            im += a.real() * b.imag() + a.imag() * b.real();
        }
        acc += re * re + im * im;
    }
    return acc / K;
}

/**
 * Oversampled 2D DFT beam (the form of the TS 38.214 Type I DFT beams), element
 * n = row * cols + col (UniformPlanarArray::GetElementLocation: y' = col, z' = row):
 *   w[n] = exp(-j 2 pi (col p / Ph + row q / Pv)) / sqrt(N),  Ph = Oh cols, Pv = Ov rows.
 */
static PhasedArrayModel::ComplexVector
DftBeam(uint32_t rows, uint32_t cols, uint32_t oh, uint32_t ov, uint32_t p, uint32_t q)
{
    const uint64_t ph = uint64_t(oh) * cols;
    const uint64_t pv = uint64_t(ov) * rows;
    const size_t n = size_t(rows) * cols;
    PhasedArrayModel::ComplexVector w(n);
    const double norm = 1.0 / std::sqrt(double(n));
    for (uint32_t r = 0; r < rows; ++r)
    {
        for (uint32_t c = 0; c < cols; ++c)
        {
            const double a = -2 * M_PI *
                             (double((uint64_t(c) * p) % ph) / double(ph) +
                              double((uint64_t(r) * q) % pv) / double(pv));
            w[size_t(r) * cols + c] = std::polar(norm, a);
        }
    }
    return w;
}

struct CodebookResult
{
    double gain{-1};  // best beam of the oversampled grid (Oh x Ov)
    uint32_t p{0};
    uint32_t q{0};
    double gain1{-1}; // best beam of the orthogonal subgrid (p % Oh == 0, q % Ov == 0)
    uint32_t p1{0};
    uint32_t q1{0};
};

/**
 * Exhaustive search of the oversampled 2D DFT codebook (Oh cols x Ov rows beams) for
 * the beam with the largest mean RB gain, using the separable structure
 * g_i(p, q) = (1/sqrt N) sum_row Wv[row][q] sum_col H_i[row][col] Wh[col][p].
 * Ties keep the first beam in (q, p) order.
 */
static CodebookResult
CodebookSearch(const std::vector<Cplx>& h, size_t K, uint32_t rows, uint32_t cols, uint32_t oh, uint32_t ov)
{
    const size_t ph = size_t(oh) * cols;
    const size_t pv = size_t(ov) * rows;
    const size_t N = size_t(rows) * cols;
    std::vector<double> whr(cols * ph);
    std::vector<double> whi(cols * ph);
    std::vector<double> wvr(rows * pv);
    std::vector<double> wvi(rows * pv);
    for (size_t c = 0; c < cols; ++c)
    {
        for (size_t p = 0; p < ph; ++p)
        {
            const double a = -2 * M_PI * double((c * p) % ph) / double(ph);
            whr[c * ph + p] = std::cos(a);
            whi[c * ph + p] = std::sin(a);
        }
    }
    for (size_t r = 0; r < rows; ++r)
    {
        for (size_t q = 0; q < pv; ++q)
        {
            const double a = -2 * M_PI * double((r * q) % pv) / double(pv);
            wvr[r * pv + q] = std::cos(a);
            wvi[r * pv + q] = std::sin(a);
        }
    }
    std::vector<double> acc(ph * pv, 0.0);
    std::vector<double> tr(rows * ph);
    std::vector<double> ti(rows * ph);
    std::vector<double> gr(ph);
    std::vector<double> gi(ph);
    for (size_t k = 0; k < K; ++k)
    {
        std::fill(tr.begin(), tr.end(), 0.0);
        std::fill(ti.begin(), ti.end(), 0.0);
        for (size_t r = 0; r < rows; ++r)
        {
            for (size_t c = 0; c < cols; ++c)
            {
                const Cplx x = h[k * N + r * cols + c];
                const double xr = x.real();
                const double xi = x.imag();
                const double* ar = &whr[c * ph];
                const double* ai = &whi[c * ph];
                double* outr = &tr[r * ph];
                double* outi = &ti[r * ph];
                for (size_t p = 0; p < ph; ++p)
                {
                    outr[p] += xr * ar[p] - xi * ai[p];
                    outi[p] += xr * ai[p] + xi * ar[p];
                }
            }
        }
        for (size_t q = 0; q < pv; ++q)
        {
            std::fill(gr.begin(), gr.end(), 0.0);
            std::fill(gi.begin(), gi.end(), 0.0);
            for (size_t r = 0; r < rows; ++r)
            {
                const double vr = wvr[r * pv + q];
                const double vi = wvi[r * pv + q];
                const double* inr = &tr[r * ph];
                const double* ini = &ti[r * ph];
                for (size_t p = 0; p < ph; ++p)
                {
                    gr[p] += vr * inr[p] - vi * ini[p];
                    gi[p] += vr * ini[p] + vi * inr[p];
                }
            }
            double* a = &acc[q * ph];
            for (size_t p = 0; p < ph; ++p)
            {
                a[p] += gr[p] * gr[p] + gi[p] * gi[p];
            }
        }
    }
    CodebookResult out;
    const double scale = 1.0 / (double(K) * double(N));
    for (size_t q = 0; q < pv; ++q)
    {
        for (size_t p = 0; p < ph; ++p)
        {
            const double g = acc[q * ph + p] * scale;
            if (g > out.gain)
            {
                out.gain = g;
                out.p = p;
                out.q = q;
            }
            if (p % oh == 0 && q % ov == 0 && g > out.gain1)
            {
                out.gain1 = g;
                out.p1 = p;
                out.q1 = q;
            }
        }
    }
    return out;
}

/** Gram matrix G = A^H A of the per-RB vectors (K x K Hermitian), G[i][j] = h_i^H h_j. */
static std::vector<Cplx>
GramMatrix(const std::vector<Cplx>& h, size_t K, size_t N)
{
    std::vector<Cplx> g(K * K);
    for (size_t i = 0; i < K; ++i)
    {
        for (size_t j = i; j < K; ++j)
        {
            double re = 0;
            double im = 0;
            for (size_t n = 0; n < N; ++n)
            {
                const Cplx a = h[i * N + n];
                const Cplx b = h[j * N + n];
                // conj(a) * b
                re += a.real() * b.real() + a.imag() * b.imag();
                im += a.real() * b.imag() - a.imag() * b.real();
            }
            g[i * K + j] = Cplx(re, im);
            g[j * K + i] = Cplx(re, -im);
        }
    }
    return g;
}

/** True if the Hermitian matrix c I - G is positive definite (complex Cholesky). */
static bool
ShiftedIsPositiveDefinite(const std::vector<Cplx>& g, size_t n, double c)
{
    std::vector<Cplx> l(n * n, Cplx(0, 0));
    for (size_t j = 0; j < n; ++j)
    {
        double d = c - g[j * n + j].real();
        for (size_t k = 0; k < j; ++k)
        {
            d -= std::norm(l[j * n + k]);
        }
        if (!(d > 0))
        {
            return false;
        }
        const double ljj = std::sqrt(d);
        l[j * n + j] = Cplx(ljj, 0);
        for (size_t i = j + 1; i < n; ++i)
        {
            Cplx s = -g[i * n + j];
            for (size_t k = 0; k < j; ++k)
            {
                s -= l[i * n + k] * std::conj(l[j * n + k]);
            }
            l[i * n + j] = s / ljj;
        }
    }
    return true;
}

struct EigenResult
{
    double mu{0};          // Rayleigh quotient x^H G x of the returned unit vector x
    std::vector<Cplx> x;   // K-dim unit vector
    uint32_t iterations{0};
    bool certified{false}; // (1 + eps) mu I - G positive definite: lambda_max <= (1 + eps) mu
};

/**
 * Largest eigenvalue of the Hermitian PSD Gram matrix by power iteration. Since mu is a
 * Rayleigh quotient, mu <= lambda_max always; the Cholesky certificate gives
 * lambda_max <= (1 + eps) mu. Starts: the all-ones vector, then the unit vector of the
 * largest diagonal entry.
 */
static EigenResult
PrincipalEigen(const std::vector<Cplx>& g, size_t n, double eps, uint32_t maxIter)
{
    EigenResult res;
    std::vector<Cplx> y(n);
    size_t jmax = 0;
    for (size_t j = 1; j < n; ++j)
    {
        if (g[j * n + j].real() > g[jmax * n + jmax].real())
        {
            jmax = j;
        }
    }
    uint32_t total = 0;
    for (int start = 0; start < 2; ++start)
    {
        std::vector<Cplx> x(n, Cplx(start == 0 ? 1.0 / std::sqrt(double(n)) : 0.0, 0.0));
        if (start == 1)
        {
            x[jmax] = Cplx(1, 0);
        }
        for (uint32_t it = 1; it <= maxIter; ++it)
        {
            ++total;
            double nrm = 0;
            double mu = 0;
            for (size_t i = 0; i < n; ++i)
            {
                double re = 0;
                double im = 0;
                for (size_t j = 0; j < n; ++j)
                {
                    const Cplx a = g[i * n + j];
                    const Cplx b = x[j];
                    re += a.real() * b.real() - a.imag() * b.imag();
                    im += a.real() * b.imag() + a.imag() * b.real();
                }
                y[i] = Cplx(re, im);
                nrm += re * re + im * im;
                mu += x[i].real() * re + x[i].imag() * im; // Re(conj(x_i) y_i)
            }
            nrm = std::sqrt(nrm);
            if (it % 20 == 0 || it == maxIter)
            {
                // certify the CURRENT x (mu is its Rayleigh quotient)
                if (ShiftedIsPositiveDefinite(g, n, (1 + eps) * mu))
                {
                    res.mu = mu;
                    res.x = x;
                    res.iterations = total;
                    res.certified = true;
                    return res;
                }
            }
            for (size_t i = 0; i < n; ++i)
            {
                x[i] = y[i] / nrm;
            }
            res.mu = mu;
        }
        res.x = x;
    }
    res.iterations = total;
    res.certified = false;
    return res;
}

/** Unit-norm eigen beam w = conj(A x) / ||A x|| (frequency-flat, unconstrained modulus). */
static PhasedArrayModel::ComplexVector
EigenBeam(const std::vector<Cplx>& h, size_t K, size_t N, const std::vector<Cplx>& x)
{
    PhasedArrayModel::ComplexVector w(N);
    double nrm = 0;
    for (size_t n = 0; n < N; ++n)
    {
        double re = 0;
        double im = 0;
        for (size_t k = 0; k < K; ++k)
        {
            const Cplx a = h[k * N + n];
            const Cplx b = x[k];
            re += a.real() * b.real() - a.imag() * b.imag();
            im += a.real() * b.imag() + a.imag() * b.real();
        }
        w[n] = Cplx(re, -im); // conj
        nrm += re * re + im * im;
    }
    nrm = std::sqrt(nrm);
    for (size_t n = 0; n < N; ++n)
    {
        w[n] = w[n] / nrm;
    }
    return w;
}

/** Constant-modulus (phase-only) version of a beam: exp(j arg w_n) / sqrt(N). */
static PhasedArrayModel::ComplexVector
PhaseOnly(const PhasedArrayModel::ComplexVector& w)
{
    const size_t n = w.GetSize();
    PhasedArrayModel::ComplexVector out(n);
    const double norm = 1.0 / std::sqrt(double(n));
    for (size_t k = 0; k < n; ++k)
    {
        out[k] = std::polar(norm, std::arg(w[k]));
    }
    return out;
}

/** Peak resident set size of this process [kB] (Linux getrusage). */
static long
PeakRssKb()
{
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return ru.ru_maxrss;
}

// Power-iteration settings of E1.5.2-1 (pre-registered): eps = 1e-9, at most 200000
// iterations per start.
static constexpr double kEigEps = 1e-9;
static constexpr uint32_t kEigMaxIter = 200000;

/**
 * Mode "chan": channel-level gain of the gNB array in the TR 38.901 channel
 * (ns-3 ThreeGppChannelModel + ThreeGppSpectrumPropagationLossModel, the
 * objects 5G-LENA uses), without the NR stack. Each link is evaluated on
 * 'copies' co-located UE nodes: ns-3 draws the large- and small-scale
 * parameters independently per node pair, so the copies are independent
 * realizations. The UE has one isotropic element. No path loss is applied:
 * the gain is sum(rx PSD) / sum(tx PSD) over the RBs, i.e. the linear mean
 * over RBs of the beamformed channel power gain.
 */
static int
RunChannelMode(const std::string& linksFile,
               double frequency,
               uint32_t numRbs,
               double scs,
               Vector gnbPos,
               uint32_t rows,
               uint32_t cols,
               const std::string& gnbElement,
               uint32_t copies,
               const std::string& gnbRole,
               const std::string& gnbWeights)
{
    NS_ABORT_MSG_IF(gnbRole != "s" && gnbRole != "u", "gnbRole must be s or u");
    NS_ABORT_MSG_IF(gnbWeights != "dp" && gnbWeights != "dpconj", "gnbWeights must be dp or dpconj");
    NS_ABORT_MSG_IF(copies == 0, "copies must be >= 1");
    auto links = ReadLinks(linksFile);
    NodeContainer gnb;
    gnb.Create(1);
    NodeContainer ues;
    ues.Create(links.size() * copies);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(gnb);
    mobility.Install(ues);
    Ptr<MobilityModel> gMob = gnb.Get(0)->GetObject<MobilityModel>();
    gMob->SetPosition(gnbPos);

    auto cond = CreateObject<CmwavePrescribedConditionModel>();
    cond->SetGnbNodeId(gnb.Get(0)->GetId());
    std::vector<Ptr<UniformPlanarArray>> ueArrays;
    for (size_t l = 0; l < links.size(); ++l)
    {
        for (uint32_t c = 0; c < copies; ++c)
        {
            Ptr<Node> n = ues.Get(l * copies + c);
            n->GetObject<MobilityModel>()->SetPosition(links[l].pos);
            cond->SetCondition(n->GetId(), links[l].los, links[l].o2i);
            ueArrays.push_back(MakeIsoArray());
        }
    }
    std::map<double, Ptr<UniformPlanarArray>> sectorArray;
    for (const auto& l : links)
    {
        if (!sectorArray.count(l.bearingDeg))
        {
            sectorArray[l.bearingDeg] = MakeGnbArray(rows, cols, l.bearingDeg, gnbElement);
        }
    }

    auto slm = CreateObject<ThreeGppSpectrumPropagationLossModel>();
    slm->SetChannelModelAttribute("Frequency", DoubleValue(frequency));
    slm->SetChannelModelAttribute("Scenario", StringValue("UMa"));
    slm->SetChannelModelAttribute("ChannelConditionModel", PointerValue(cond));
    slm->AssignStreams(1000);
    DoubleValue fRead;
    slm->GetChannelModelAttribute("Frequency", fRead);
    StringValue scRead;
    slm->GetChannelModelAttribute("Scenario", scRead);
    auto txParams = FlatTxParams(numRbs, frequency, scs);
    const double txSum = Sum(*txParams->psd);

    std::ofstream det("e152_det.csv");
    det << "link_index,name,bearing_deg,zenith_deg,azimuth_deg,elem_gain_db,af_s_db,af_u_db,n_elems\n";
    std::ofstream real("e152_real.csv");
    real << "link_index,copy,gain_lin,gnb_is_s\n";
    for (size_t l = 0; l < links.size(); ++l)
    {
        auto gUpa = sectorArray.at(links[l].bearingDeg);
        for (uint32_t c = 0; c < copies; ++c)
        {
            const size_t idx = l * copies + c;
            Ptr<MobilityModel> uMob = ues.Get(idx)->GetObject<MobilityModel>();
            auto uUpa = ueArrays[idx];
            PhasedArrayModel::ComplexVector w = CreateDirectPathBfv(gMob, uMob, gUpa);
            if (c == 0)
            {
                Angles ang(uMob->GetPosition(), gMob->GetPosition());
                double e = 0;
                double s = 0;
                double u = 0;
                DeterministicGain(gUpa, w, ang, &e, &s, &u);
                det << l << ',' << links[l].name << ',' << std::setprecision(17)
                    << links[l].bearingDeg << ',' << ang.GetInclination() * 180.0 / M_PI << ','
                    << ang.GetAzimuth() * 180.0 / M_PI << ',' << 10 * std::log10(e) << ','
                    << 10 * std::log10(s) << ',' << 10 * std::log10(u) << ',' << gUpa->GetNumElems()
                    << '\n';
            }
            if (gnbWeights == "dpconj")
            {
                for (size_t k = 0; k < w.GetSize(); ++k)
                {
                    w[k] = std::conj(w[k]);
                }
            }
            gUpa->SetBeamformingVector(w);
            uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
            Ptr<SpectrumSignalParameters> rx =
                gnbRole == "s"
                    ? slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa)
                    : slm->CalcRxPowerSpectralDensity(txParams, uMob, gMob, uUpa, gUpa);
            auto m = slm->GetChannelModel()->GetChannel(gMob, uMob, gUpa, uUpa);
            const bool gnbIsS = !m->IsReverse(gUpa->GetId(), uUpa->GetId());
            real << l << ',' << c << ',' << std::setprecision(17) << Sum(*rx->psd) / txSum << ','
                 << gnbIsS << '\n';
        }
    }
    std::ofstream info("e152_info.csv");
    info << "key,value\n"
         << "mode,chan\n"
         << "frequency_hz_readback," << std::setprecision(17) << fRead.Get() << '\n'
         << "scenario_readback," << scRead.Get() << '\n'
         << "num_rbs," << numRbs << '\n'
         << "scs_hz," << scs << '\n'
         << "rows," << rows << '\n'
         << "cols," << cols << '\n'
         << "gnb_element," << gnbElement << '\n'
         << "copies," << copies << '\n'
         << "gnb_role," << gnbRole << '\n'
         << "gnb_weights," << gnbWeights << '\n'
         << "n_links," << links.size() << '\n';
    Simulator::Destroy();
    return 0;
}

/**
 * Mode "beams" (E1.5.2-1): on each stored channel realization (set-up of mode chan,
 * gNB = transmit side s, UE = one isotropic element), the mean RB gain of
 *   dp    NR DirectPath vector (geometric steering)          ns-3 and rebuilt
 *   cb    best beam of the oversampled DFT codebook          rebuilt; ns-3 with it installed
 *   cb1   best beam of its orthogonal subgrid                rebuilt
 *   eig   largest eigenvalue of R = (1/K) sum_i conj(h_i) h_i^T, the maximum of w^H R w
 *         over unit-norm frequency-flat w                    rebuilt; ns-3 with its eigenvector
 *   eigpo phase-only version of the eigen beam               rebuilt
 *   mrt   (1/K) sum_i ||h_i||^2, the per-RB matched-filter ceiling
 * Outputs: e1521_real.csv, e1521_det.csv, e1521_info.csv; for copies < dumpCopies the
 * per-RB vectors h (K x N complex doubles, little-endian, row-major) in e1521_h.bin,
 * indexed by e1521_h_index.csv.
 */
static int
RunBeamsMode(const std::string& linksFile,
             double frequency,
             uint32_t numRbs,
             double scs,
             Vector gnbPos,
             uint32_t rows,
             uint32_t cols,
             uint32_t copies,
             uint32_t oversampling,
             uint32_t dumpCopies)
{
    NS_ABORT_MSG_IF(copies == 0, "copies must be >= 1");
    NS_ABORT_MSG_IF(oversampling == 0, "oversampling must be >= 1");
    auto links = ReadLinks(linksFile);
    for (const auto& l : links)
    {
        // ns-3.48 UMa: O2I selects the O2I table (6x6 correlation, no K-factor) but LOS
        // still runs the 7-parameter LOS generation and adds a LOS ray (hazard 13).
        NS_ABORT_MSG_IF(l.los && l.o2i, "mode beams: LOS with O2I is not allowed (hazard 13)");
    }
    NodeContainer gnb;
    gnb.Create(1);
    NodeContainer ues;
    ues.Create(links.size() * copies);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(gnb);
    mobility.Install(ues);
    Ptr<MobilityModel> gMob = gnb.Get(0)->GetObject<MobilityModel>();
    gMob->SetPosition(gnbPos);

    auto cond = CreateObject<CmwavePrescribedConditionModel>();
    cond->SetGnbNodeId(gnb.Get(0)->GetId());
    std::vector<Ptr<UniformPlanarArray>> ueArrays;
    for (size_t l = 0; l < links.size(); ++l)
    {
        for (uint32_t c = 0; c < copies; ++c)
        {
            Ptr<Node> n = ues.Get(l * copies + c);
            n->GetObject<MobilityModel>()->SetPosition(links[l].pos);
            cond->SetCondition(n->GetId(), links[l].los, links[l].o2i);
            ueArrays.push_back(MakeIsoArray());
        }
    }
    std::map<double, Ptr<UniformPlanarArray>> sectorArray;
    for (const auto& l : links)
    {
        if (!sectorArray.count(l.bearingDeg))
        {
            sectorArray[l.bearingDeg] = MakeGnbArray(rows, cols, l.bearingDeg, "3gpp");
        }
    }
    for (const auto& [b, a] : sectorArray)
    {
        NS_ABORT_MSG_IF(a->GetNumPorts() != 1 || a->IsDualPol(), "mode beams needs one port, single polarization");
    }

    auto slm = CreateObject<ThreeGppSpectrumPropagationLossModel>();
    slm->SetChannelModelAttribute("Frequency", DoubleValue(frequency));
    slm->SetChannelModelAttribute("Scenario", StringValue("UMa"));
    slm->SetChannelModelAttribute("ChannelConditionModel", PointerValue(cond));
    slm->AssignStreams(1000);
    DoubleValue fRead;
    slm->GetChannelModelAttribute("Frequency", fRead);
    StringValue scRead;
    slm->GetChannelModelAttribute("Scenario", scRead);
    auto txParams = FlatTxParams(numRbs, frequency, scs);
    const double txSum = Sum(*txParams->psd);
    const size_t K = txParams->psd->GetValuesN();
    const size_t N = size_t(rows) * cols;

    std::ofstream det("e1521_det.csv");
    det << "link_index,name,bearing_deg,zenith_deg,azimuth_deg,elem_gain_db,af_s_db,af_u_db,n_elems\n";
    std::ofstream real("e1521_real.csv");
    real << "link_index,copy,n_clusters,gnb_is_s,g_ns3_dp,g_dp,g_cb,cb_p,cb_q,g_cb1,cb1_p,cb1_q,"
            "g_eigpo,g_eig,g_mrt,g_ns3_cb,g_ns3_eig,g_eigvec,eig_iters,eig_certified\n";
    std::ofstream hidx("e1521_h_index.csv");
    hidx << "link_index,copy,k,n,offset_bytes\n";
    std::ofstream hbin("e1521_h.bin", std::ios::binary);
    uint64_t offset = 0;
    uint64_t uncertified = 0;
    for (size_t l = 0; l < links.size(); ++l)
    {
        auto gUpa = sectorArray.at(links[l].bearingDeg);
        for (uint32_t c = 0; c < copies; ++c)
        {
            const size_t idx = l * copies + c;
            Ptr<MobilityModel> uMob = ues.Get(idx)->GetObject<MobilityModel>();
            auto uUpa = ueArrays[idx];
            PhasedArrayModel::ComplexVector wDp = CreateDirectPathBfv(gMob, uMob, gUpa);
            if (c == 0)
            {
                Angles ang(uMob->GetPosition(), gMob->GetPosition());
                double e = 0;
                double s = 0;
                double u = 0;
                DeterministicGain(gUpa, wDp, ang, &e, &s, &u);
                det << l << ',' << links[l].name << ',' << std::setprecision(17)
                    << links[l].bearingDeg << ',' << ang.GetInclination() * 180.0 / M_PI << ','
                    << ang.GetAzimuth() * 180.0 / M_PI << ',' << 10 * std::log10(e) << ','
                    << 10 * std::log10(s) << ',' << 10 * std::log10(u) << ',' << gUpa->GetNumElems()
                    << '\n';
            }
            // DL first: the gNB is the transmit side s of the stored matrix.
            gUpa->SetBeamformingVector(wDp);
            uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
            const double gNs3Dp =
                Sum(*slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa)->psd) / txSum;
            auto m = slm->GetChannelModel()->GetChannel(gMob, uMob, gUpa, uUpa);
            const bool gnbIsS = !m->IsReverse(gUpa->GetId(), uUpa->GetId());
            NS_ABORT_MSG_IF(!gnbIsS, "gNB is not the transmit side s");
            auto prm = slm->GetChannelModel()->GetParams(gMob, uMob);
            const std::vector<Cplx> h = PerRbChannel(m, prm, txParams->psd);

            const double gDp = BeamGain(h, K, N, wDp);
            const CodebookResult cb = CodebookSearch(h, K, rows, cols, oversampling, oversampling);
            const std::vector<Cplx> gram = GramMatrix(h, K, N);
            const EigenResult eig = PrincipalEigen(gram, K, kEigEps, kEigMaxIter);
            uncertified += eig.certified ? 0 : 1;
            const PhasedArrayModel::ComplexVector wEig = EigenBeam(h, K, N, eig.x);
            const double gEigvec = BeamGain(h, K, N, wEig);
            const double gEigpo = BeamGain(h, K, N, PhaseOnly(wEig));
            double trace = 0;
            for (size_t k = 0; k < K; ++k)
            {
                trace += gram[k * K + k].real();
            }
            const auto wCb = DftBeam(rows, cols, oversampling, oversampling, cb.p, cb.q);
            gUpa->SetBeamformingVector(wCb);
            const double gNs3Cb =
                Sum(*slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa)->psd) / txSum;
            gUpa->SetBeamformingVector(wEig);
            const double gNs3Eig =
                Sum(*slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa)->psd) / txSum;

            real << l << ',' << c << ',' << m->m_channel.GetNumPages() << ',' << gnbIsS << ','
                 << std::setprecision(17) << gNs3Dp << ',' << gDp << ',' << cb.gain << ',' << cb.p
                 << ',' << cb.q << ',' << cb.gain1 << ',' << cb.p1 << ',' << cb.q1 << ',' << gEigpo
                 << ',' << eig.mu / K << ',' << trace / K << ',' << gNs3Cb << ',' << gNs3Eig << ','
                 << gEigvec << ',' << eig.iterations << ',' << eig.certified << '\n';
            if (c < dumpCopies)
            {
                hidx << l << ',' << c << ',' << K << ',' << N << ',' << offset << '\n';
                for (const Cplx& v : h)
                {
                    const double re = v.real();
                    const double im = v.imag();
                    hbin.write(reinterpret_cast<const char*>(&re), sizeof(double));
                    hbin.write(reinterpret_cast<const char*>(&im), sizeof(double));
                }
                offset += uint64_t(h.size()) * 2 * sizeof(double);
            }
        }
    }
    hbin.close();
    std::ofstream info("e1521_info.csv");
    info << "key,value\n"
         << "mode,beams\n"
         << "frequency_hz_readback," << std::setprecision(17) << fRead.Get() << '\n'
         << "scenario_readback," << scRead.Get() << '\n'
         << "num_rbs," << K << '\n'
         << "scs_hz," << scs << '\n'
         << "rows," << rows << '\n'
         << "cols," << cols << '\n'
         << "oversampling," << oversampling << '\n'
         << "copies," << copies << '\n'
         << "dump_copies," << dumpCopies << '\n'
         << "n_links," << links.size() << '\n'
         << "rng_seed," << RngSeedManager::GetSeed() << '\n'
         << "rng_run," << RngSeedManager::GetRun() << '\n'
         << "eig_eps," << kEigEps << '\n'
         << "eig_max_iter," << kEigMaxIter << '\n'
         << "eig_uncertified," << uncertified << '\n'
         << "peak_rss_kb," << PeakRssKb() << '\n';
    Simulator::Destroy();
    return 0;
}

/**
 * E1.5.2-1: the gNB beam toward one UE computed from the stored channel, for mode
 * nrfading with --gnbBeam=cb|eig. The channel is generated here in the DL direction
 * (gNB = s) with the DirectPath vectors installed, exactly as in mode beams.
 */
static PhasedArrayModel::ComplexVector
ChannelBeam(const std::string& kind,
            Ptr<ThreeGppSpectrumPropagationLossModel> slm,
            Ptr<MobilityModel> gMob,
            Ptr<MobilityModel> uMob,
            Ptr<UniformPlanarArray> gUpa,
            Ptr<UniformPlanarArray> uUpa,
            Ptr<SpectrumSignalParameters> txParams,
            uint32_t rows,
            uint32_t cols,
            uint32_t oversampling)
{
    gUpa->SetBeamformingVector(CreateDirectPathBfv(gMob, uMob, gUpa));
    uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
    slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa);
    auto m = slm->GetChannelModel()->GetChannel(gMob, uMob, gUpa, uUpa);
    NS_ABORT_MSG_IF(m->IsReverse(gUpa->GetId(), uUpa->GetId()), "gNB is not the transmit side s");
    if (kind == "dpid")
    {
        // E1.5.4-0: the DirectPath vector itself, saved with a distinct BeamId per UE
        return CreateDirectPathBfv(gMob, uMob, gUpa);
    }
    const std::vector<Cplx> h =
        PerRbChannel(m, slm->GetChannelModel()->GetParams(gMob, uMob), txParams->psd);
    const size_t K = txParams->psd->GetValuesN();
    const size_t N = size_t(rows) * cols;
    if (kind == "cb")
    {
        const CodebookResult cb = CodebookSearch(h, K, rows, cols, oversampling, oversampling);
        return DftBeam(rows, cols, oversampling, oversampling, cb.p, cb.q);
    }
    NS_ABORT_MSG_IF(kind != "eig", "gnbBeam must be dp, cb or eig");
    const EigenResult eig = PrincipalEigen(GramMatrix(h, K, N), K, kEigEps, kEigMaxIter);
    NS_ABORT_MSG_IF(!eig.certified, "eigenvalue not certified");
    return EigenBeam(h, K, N, eig.x);
}

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

/**
 * Mode "nrfading": the NR stack of mode "link" with the TR 38.901 UMa fast-fading
 * channel (ThreeGppSpectrumPropagationLossModel, prescribed condition per link),
 * the prescribed large-scale loss (MatrixPropagationLossModel), one gNB sector
 * (UPA rows x cols, TR 38.901 element, given bearing, downtilt 0) and UEs with one
 * isotropic element. DL traffic only. After the run, on the same channel objects,
 * the gain of every gNB-UE link with the NR DirectPath beams is recomputed
 * (e152_nr_direct.csv) together with the role of the gNB in the stored channel
 * matrix, so that every DL TB SINR can be compared with it.
 */
// ---------------------------------------------------------------------------
// E1.5.4-0 (OFDMA co-scheduling diagnostic), mode nrfading with --ofdmaDiag=1:
//   e15od_dci.csv    every DL data DCI sent by the gNB, in PHY send order within the slot,
//                    with its RBG bitmask (GnbPhyTxedCtrlMsgsTrace)
//   e15od_dl_tb.csv  every DL TB at the UE with frame/subframe/slot/symbol (RxPacketTraceUe)
//   e15od_rb_gain.csv after the run: per-RB gain of UE j when the gNB holds UE b's beam
// ---------------------------------------------------------------------------
static std::ofstream g_odDci;
static std::ofstream g_odTb;
static int64_t g_odSlotKey{-1};
static uint32_t g_odSeq{0};

static void
OdDciTrace(SfnSf sfn, uint16_t /* cellId */, uint16_t /* rnti */, uint8_t /* bwpId */,
           Ptr<const NrControlMessage> msg)
{
    if (msg->GetMessageType() != NrControlMessage::DL_DCI)
    {
        return;
    }
    // GetDciInfoElement() is not const in NR; the message is only read here.
    auto dciMsg = DynamicCast<NrDlDciMessage>(ConstCast<NrControlMessage>(msg));
    NS_ABORT_MSG_IF(!dciMsg, "DL_DCI message is not an NrDlDciMessage");
    auto dci = dciMsg->GetDciInfoElement();
    const int64_t key = int64_t(sfn.GetFrame()) * 1000 + sfn.GetSubframe() * 50 + sfn.GetSlot();
    if (key != g_odSlotKey)
    {
        g_odSlotKey = key;
        g_odSeq = 0;
    }
    std::string mask;
    for (size_t k = 0; k < dci->m_rbgBitmask.size(); ++k)
    {
        mask += dci->m_rbgBitmask[k] ? '1' : '0';
    }
    g_odDci << Simulator::Now().GetNanoSeconds() << ',' << sfn.GetFrame() << ','
            << +sfn.GetSubframe() << ',' << +sfn.GetSlot() << ',' << g_odSeq++ << ',' << dci->m_rnti
            << ',' << +dci->m_symStart << ',' << +dci->m_numSym << ',' << +dci->m_mcs << ','
            << +dci->m_rv << ',' << mask << '\n';
}

// MAC allocation order (NrGnbMac::DoSchedConfigIndication fires DlScheduling while
// iterating the slot allocation list that it hands to the PHY, which schedules the
// allocations in that order). The DCI trace order is NOT this order: the PHY sorts its
// control-message list (std::list<Ptr<...>>::sort, i.e. by pointer value).
static std::ofstream g_odSched;
static int64_t g_odSchedKey{-1};
static uint32_t g_odSchedSeq{0};

static void
OdDlSchedTrace(NrSchedulingCallbackInfo s)
{
    const int64_t key = int64_t(s.m_frameNum) * 1000 + s.m_subframeNum * 50 + s.m_slotNum;
    if (key != g_odSchedKey)
    {
        g_odSchedKey = key;
        g_odSchedSeq = 0;
    }
    g_odSched << Simulator::Now().GetNanoSeconds() << ',' << s.m_frameNum << ',' << +s.m_subframeNum
              << ',' << s.m_slotNum << ',' << g_odSchedSeq++ << ',' << s.m_rnti << ','
              << +s.m_symStart << ',' << +s.m_numSym << ',' << +s.m_mcs << ',' << s.m_tbSize << ','
              << +s.m_rv << '\n';
}

static void
OdTbTrace(uint32_t ueIndex, RxPacketTraceParams p)
{
    g_odTb << ueIndex << ',' << p.m_rnti << ',' << Simulator::Now().GetNanoSeconds() << ','
           << p.m_frameNum << ',' << +p.m_subframeNum << ',' << p.m_slotNum << ','
           << +p.m_symStart << ',' << +p.m_numSym << ',' << std::setprecision(17) << p.m_sinr
           << ',' << p.m_rbAssignedNum << ',' << +p.m_mcs << ',' << +p.m_rv << ','
           << p.m_corrupt << '\n';
}

static int
RunNrFadingMode(const std::string& linksFile,
                double frequency,
                double bandwidth,
                uint16_t numerology,
                double rbOverhead,
                double gnbTxPower,
                double nfGnb,
                double nfUe,
                Vector gnbPos,
                uint32_t rows,
                uint32_t cols,
                double bearingDeg,
                const std::string& scheduler,
                double dlRateBps,
                uint32_t packetSize,
                double appStart,
                double simTime,
                const std::string& tddPattern,
                const std::string& gnbBeam,
                uint32_t oversampling,
                bool ofdmaDiag)
{
    NS_ABORT_MSG_IF(gnbBeam != "dp" && gnbBeam != "cb" && gnbBeam != "eig" && gnbBeam != "dpid",
                    "gnbBeam must be dp, cb, eig or dpid");
    auto links = ReadLinks(linksFile);
    const uint32_t nUe = links.size();
    Config::SetDefault("ns3::NrRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    NodeContainer gnbNodes;
    gnbNodes.Create(1);
    NodeContainer ueNodes;
    ueNodes.Create(nUe);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(gnbNodes);
    mobility.Install(ueNodes);
    Ptr<MobilityModel> gMob = gnbNodes.Get(0)->GetObject<MobilityModel>();
    gMob->SetPosition(gnbPos);
    auto cond = CreateObject<CmwavePrescribedConditionModel>();
    cond->SetGnbNodeId(gnbNodes.Get(0)->GetId());
    auto lossModel = CreateObject<MatrixPropagationLossModel>();
    lossModel->SetDefaultLoss(400.0); // UE-UE pairs
    for (uint32_t i = 0; i < nUe; ++i)
    {
        ueNodes.Get(i)->GetObject<MobilityModel>()->SetPosition(links[i].pos);
        cond->SetCondition(ueNodes.Get(i)->GetId(), links[i].los, links[i].o2i);
        lossModel->SetLoss(gMob, ueNodes.Get(i)->GetObject<MobilityModel>(), links[i].lossDb, true);
    }

    CcBwpCreator ccBwpCreator;
    CcBwpCreator::SimpleOperationBandConf bandConf(frequency, bandwidth, 1);
    OperationBandInfo band = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);
    auto channelHelper = CreateObject<NrChannelHelper>();
    Ptr<SpectrumChannel> channel = channelHelper->CreateChannel(0);
    channel->AddPropagationLossModel(lossModel);
    auto slm = CreateObject<ThreeGppSpectrumPropagationLossModel>();
    slm->SetChannelModelAttribute("Frequency", DoubleValue(frequency));
    slm->SetChannelModelAttribute("Scenario", StringValue("UMa"));
    slm->SetChannelModelAttribute("ChannelConditionModel", PointerValue(cond));
    slm->AssignStreams(1000);
    channel->AddPhasedArraySpectrumPropagationLossModel(slm);
    band.GetBwpAt(0, 0)->SetChannel(channel);
    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({band});

    auto epcHelper = CreateObject<NrPointToPointEpcHelper>();
    auto bfHelper = CreateObject<IdealBeamformingHelper>();
    bfHelper->SetAttribute("BeamformingMethod", TypeIdValue(DirectPathBeamforming::GetTypeId()));
    if (gnbBeam != "dp")
    {
        // E1.5.2-1: the DirectPath task runs once at attach and is then overwritten with
        // the channel-computed beam; no periodic re-run within the simulation.
        bfHelper->SetAttribute("BeamformingPeriodicity", TimeValue(Seconds(simTime + 100.0)));
    }
    auto nrHelper = CreateObject<NrHelper>();
    nrHelper->SetBeamformingHelper(bfHelper);
    nrHelper->SetEpcHelper(epcHelper);
    nrHelper->SetAttribute("RbOverhead", DoubleValue(rbOverhead));
    nrHelper->SetSchedulerTypeId(TypeId::LookupByName(scheduler));
    nrHelper->SetGnbPhyAttribute("Numerology", UintegerValue(numerology));
    nrHelper->SetGnbPhyAttribute("Pattern", StringValue(tddPattern));
    nrHelper->SetUePhyAttribute("EnableRlfDetection", BooleanValue(false));
    nrHelper->SetGnbPhyAttribute("TxPower", DoubleValue(gnbTxPower));
    nrHelper->SetGnbPhyAttribute("NoiseFigure", DoubleValue(nfGnb));
    nrHelper->SetGnbPhyAttribute(
        "PowerAllocationType",
        EnumValue(NrSpectrumValueHelper::UNIFORM_POWER_ALLOCATION_BW));
    nrHelper->SetUePhyAttribute("TxPower", DoubleValue(23.0));
    nrHelper->SetUePhyAttribute("NoiseFigure", DoubleValue(nfUe));
    nrHelper->SetUePhyAttribute("EnableUplinkPowerControl", BooleanValue(false));
    nrHelper->SetUeAntennaAttribute("NumRows", UintegerValue(1));
    nrHelper->SetUeAntennaAttribute("NumColumns", UintegerValue(1));
    nrHelper->SetUeAntennaAttribute("AntennaElement",
                                    PointerValue(CreateObject<IsotropicAntennaModel>()));
    nrHelper->SetGnbAntennaAttribute("NumRows", UintegerValue(rows));
    nrHelper->SetGnbAntennaAttribute("NumColumns", UintegerValue(cols));
    nrHelper->SetGnbAntennaAttribute("AntennaHorizontalSpacing", DoubleValue(0.5));
    nrHelper->SetGnbAntennaAttribute("AntennaVerticalSpacing", DoubleValue(0.5));
    nrHelper->SetGnbAntennaAttribute("BearingAngle", DoubleValue(BearingRad(bearingDeg)));
    nrHelper->SetGnbAntennaAttribute("DowntiltAngle", DoubleValue(0.0));
    nrHelper->SetGnbAntennaAttribute("AntennaElement",
                                     PointerValue(CreateObject<ThreeGppAntennaModel>()));

    NetDeviceContainer gnbDevs = nrHelper->InstallGnbDevice(gnbNodes, allBwps);
    NetDeviceContainer ueDevs = nrHelper->InstallUeDevice(ueNodes, allBwps);
    nrHelper->AssignStreams({.gnbDevs = gnbDevs, .ueDevs = ueDevs});

    auto [remoteHost, remoteHostAddr] = epcHelper->SetupRemoteHost("100Gb/s", 2500, Seconds(0.0));
    InternetStackHelper internet;
    internet.Install(ueNodes);
    Ipv4InterfaceContainer ueIfaces = epcHelper->AssignUeIpv4Address(ueDevs);
    nrHelper->AttachToClosestGnb(ueDevs, gnbDevs);

    // E1.5.2-1: channel-computed gNB beams (codebook or eigen), one BeamId per UE.
    std::vector<PhasedArrayModel::ComplexVector> customW;
    if (gnbBeam != "dp")
    {
        auto gp0 = NrHelper::GetGnbPhy(gnbDevs.Get(0), 0);
        auto gUpa0 = DynamicCast<UniformPlanarArray>(gp0->GetSpectrumPhy()->GetAntenna());
        NS_ABORT_MSG_IF(!gUpa0, "gNB antenna is not a UniformPlanarArray");
        auto tx0 = FlatTxParams(gp0->GetRbNum(), frequency, gp0->GetSubcarrierSpacing());
        for (uint32_t i = 0; i < nUe; ++i)
        {
            Ptr<MobilityModel> uMob = ueNodes.Get(i)->GetObject<MobilityModel>();
            auto uUpa = DynamicCast<UniformPlanarArray>(
                NrHelper::GetUePhy(ueDevs.Get(i), 0)->GetSpectrumPhy()->GetAntenna());
            NS_ABORT_MSG_IF(!uUpa, "UE antenna is not a UniformPlanarArray");
            customW.push_back(
                ChannelBeam(gnbBeam, slm, gMob, uMob, gUpa0, uUpa, tx0, rows, cols, oversampling));
            gp0->GetSpectrumPhy()->GetBeamManager()->SaveBeamformingVector(
                BeamformingVector(customW.back(), BeamId(static_cast<uint16_t>(i + 1), 0.0)),
                ueDevs.Get(i));
        }
    }

    const uint16_t dlPort = 1234;
    ApplicationContainer servers;
    ApplicationContainer clients;
    servers.Add(UdpServerHelper(dlPort).Install(ueNodes));
    for (uint32_t i = 0; i < nUe; ++i)
    {
        UdpClientHelper dl(ueIfaces.GetAddress(i), dlPort);
        dl.SetAttribute("MaxPackets", UintegerValue(0xFFFFFFFF));
        dl.SetAttribute("PacketSize", UintegerValue(packetSize));
        dl.SetAttribute("Interval", TimeValue(Seconds(packetSize * 8.0 / dlRateBps)));
        clients.Add(dl.Install(remoteHost));
    }
    servers.Start(Seconds(appStart));
    clients.Start(Seconds(appStart));
    servers.Stop(Seconds(simTime));
    clients.Stop(Seconds(simTime));

    g_dlTb.open("e15_dl_tb.csv");
    g_dlTb << "ue_index,rnti,time_ns,sinr_lin,sinr_min_lin,rb_assigned,mcs,tb_size,rank,rv,num_sym,"
              "corrupt,tbler\n";
    for (uint32_t i = 0; i < nUe; ++i)
    {
        NrHelper::GetUePhy(ueDevs.Get(i), 0)
            ->GetSpectrumPhy()
            ->TraceConnectWithoutContext("RxPacketTraceUe", MakeBoundCallback(&DlTbTrace, i));
    }
    g_rrc.open("e15_rrc_events.csv");
    g_rrc << "time_ns,imsi,cell_id,rnti\n";
    DynamicCast<NrGnbNetDevice>(gnbDevs.Get(0))
        ->GetRrc()
        ->TraceConnectWithoutContext("ConnectionEstablished", MakeCallback(&ConnectionEstablished));
    Simulator::Schedule(Seconds(appStart),
                        &WriteRbAfterAttach,
                        ueDevs,
                        std::string("e15_rb_after_attach.csv"));

    auto gp = NrHelper::GetGnbPhy(gnbDevs.Get(0), 0);
    {
        DoubleValue fRead;
        slm->GetChannelModelAttribute("Frequency", fRead);
        StringValue scRead;
        slm->GetChannelModelAttribute("Scenario", scRead);
        auto up = NrHelper::GetUePhy(ueDevs.Get(0), 0);
        std::ofstream info("e15_run_info.csv");
        info << "key,value\n"
             << "mode,nrfading\n"
             << "frequency_hz," << std::setprecision(17) << frequency << '\n'
             << "channel_model_frequency_hz_readback," << fRead.Get() << '\n'
             << "channel_model_scenario_readback," << scRead.Get() << '\n'
             << "bandwidth_hz," << bandwidth << '\n'
             << "numerology," << numerology << '\n'
             << "gnb_rb_num," << gp->GetRbNum() << '\n'
             << "gnb_tx_power_dbm," << gp->GetTxPower() << '\n'
             << "gnb_noise_figure_db," << gp->GetNoiseFigure() << '\n'
             << "ue_noise_figure_db," << up->GetNoiseFigure() << '\n'
             << "rows," << rows << '\n'
             << "cols," << cols << '\n'
             << "bearing_deg," << bearingDeg << '\n'
             << "scheduler," << scheduler << '\n'
             << "tdd_pattern," << gp->GetPattern() << '\n'
             << "n_ue," << nUe << '\n'
             << "app_start_s," << appStart << '\n'
             << "sim_time_s," << simTime << '\n';
    }

    if (ofdmaDiag)
    {
        g_odDci.open("e15od_dci.csv");
        g_odDci << "time_ns,frame,subframe,slot,seq_in_slot,rnti,sym_start,num_sym,mcs,rv,rbg_bitmask\n";
        gp->TraceConnectWithoutContext("GnbPhyTxedCtrlMsgsTrace", MakeCallback(&OdDciTrace));
        g_odSched.open("e15od_dl_sched.csv");
        g_odSched << "time_ns,frame,subframe,slot,seq_in_slot,rnti,sym_start,num_sym,mcs,tb_size,rv\n";
        DynamicCast<NrGnbNetDevice>(gnbDevs.Get(0))
            ->GetMac(0)
            ->TraceConnectWithoutContext("DlScheduling", MakeCallback(&OdDlSchedTrace));
        g_odTb.open("e15od_dl_tb.csv");
        g_odTb << "ue_index,rnti,time_ns,frame,subframe,slot,sym_start,num_sym,sinr_lin,rb_assigned,mcs,"
                  "rv,corrupt\n";
        for (uint32_t i = 0; i < nUe; ++i)
        {
            NrHelper::GetUePhy(ueDevs.Get(i), 0)
                ->GetSpectrumPhy()
                ->TraceConnectWithoutContext("RxPacketTraceUe", MakeBoundCallback(&OdTbTrace, i));
        }
    }

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    auto gUpa = DynamicCast<UniformPlanarArray>(gp->GetSpectrumPhy()->GetAntenna());
    NS_ABORT_MSG_IF(!gUpa, "gNB antenna is not a UniformPlanarArray");
    auto txParams = FlatTxParams(gp->GetRbNum(), frequency, gp->GetSubcarrierSpacing());
    const double txSum = Sum(*txParams->psd);
    if (ofdmaDiag)
    {
        // Per-RB gain of UE j (rows) when the gNB array holds the beam that its beam
        // manager stores for UE b, on the same channel objects (flat unit tx PSD).
        g_odDci.close();
        g_odTb.close();
        g_odSched.close();
        auto bm = gp->GetSpectrumPhy()->GetBeamManager();
        std::ofstream rbOut("e15od_rb_gain.csv");
        rbOut << "ue_index,beam_ue_index,rb,gain_lin\n";
        for (uint32_t j = 0; j < nUe; ++j)
        {
            Ptr<MobilityModel> uMob = ueNodes.Get(j)->GetObject<MobilityModel>();
            auto uUpa = DynamicCast<UniformPlanarArray>(
                NrHelper::GetUePhy(ueDevs.Get(j), 0)->GetSpectrumPhy()->GetAntenna());
            uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
            for (uint32_t b = 0; b < nUe; ++b)
            {
                gUpa->SetBeamformingVector(bm->GetBeamformingVector(ueDevs.Get(b)));
                Ptr<SpectrumSignalParameters> rx =
                    slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa);
                for (size_t k = 0; k < rx->psd->GetValuesN(); ++k)
                {
                    rbOut << j << ',' << b << ',' << k << ',' << std::setprecision(17)
                          << (*rx->psd)[k] / (*txParams->psd)[k] << '\n';
                }
            }
        }
    }
    if (gnbBeam != "dp")
    {
        // Gain of every link with the installed channel-computed beam, on the same
        // channel objects; also whether the beam manager still holds that beam and when
        // the stored channel was generated (it must be the pre-run one, t = 0).
        auto bm = gp->GetSpectrumPhy()->GetBeamManager();
        std::ofstream beamOut("e1521_nr_beam.csv");
        beamOut << "ue_index,name,beam,gain_lin,gnb_is_s,gnb_elems,bm_vector_match,channel_generated_s\n";
        for (uint32_t i = 0; i < nUe; ++i)
        {
            Ptr<MobilityModel> uMob = ueNodes.Get(i)->GetObject<MobilityModel>();
            auto uUpa = DynamicCast<UniformPlanarArray>(
                NrHelper::GetUePhy(ueDevs.Get(i), 0)->GetSpectrumPhy()->GetAntenna());
            const bool match = bm->GetBeamformingVector(ueDevs.Get(i)) == customW[i];
            gUpa->SetBeamformingVector(customW[i]);
            uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
            auto m = slm->GetChannelModel()->GetChannel(gMob, uMob, gUpa, uUpa);
            const bool gnbIsS = !m->IsReverse(gUpa->GetId(), uUpa->GetId());
            Ptr<SpectrumSignalParameters> rx =
                slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa);
            beamOut << i << ',' << links[i].name << ',' << gnbBeam << ',' << std::setprecision(17)
                    << Sum(*rx->psd) / txSum << ',' << gnbIsS << ',' << gUpa->GetNumElems() << ','
                    << match << ',' << m->m_generatedTime.GetSeconds() << '\n';
        }
        g_dlTb.close();
        g_rrc.close();
        Simulator::Destroy();
        return 0;
    }
    std::ofstream direct("e152_nr_direct.csv");
    direct << "ue_index,name,gain_lin,gnb_is_s,gnb_elems\n";
    for (uint32_t i = 0; i < nUe; ++i)
    {
        Ptr<MobilityModel> uMob = ueNodes.Get(i)->GetObject<MobilityModel>();
        auto uUpa = DynamicCast<UniformPlanarArray>(
            NrHelper::GetUePhy(ueDevs.Get(i), 0)->GetSpectrumPhy()->GetAntenna());
        NS_ABORT_MSG_IF(!uUpa, "UE antenna is not a UniformPlanarArray");
        gUpa->SetBeamformingVector(CreateDirectPathBfv(gMob, uMob, gUpa));
        uUpa->SetBeamformingVector(CreateDirectPathBfv(uMob, gMob, uUpa));
        auto m = slm->GetChannelModel()->GetChannel(gMob, uMob, gUpa, uUpa);
        const bool gnbIsS = !m->IsReverse(gUpa->GetId(), uUpa->GetId());
        // DL direction, as for the DL TBs (the long-term component is shared by both
        // directions; the stored matrix is only transposed for the reverse one).
        Ptr<SpectrumSignalParameters> rx =
            slm->CalcRxPowerSpectralDensity(txParams, gMob, uMob, gUpa, uUpa);
        direct << i << ',' << links[i].name << ',' << std::setprecision(17) << Sum(*rx->psd) / txSum
               << ',' << gnbIsS << ',' << gUpa->GetNumElems() << '\n';
    }
    g_dlTb.close();
    g_rrc.close();
    Simulator::Destroy();
    return 0;
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
    std::string errorModel; // E1.5.3, mode link: empty = NR default (unchanged)
    cmd.AddValue("errorModel", "mode link: DL and UL error model TypeId (e.g. ns3::NrEesmIrT2); empty = NR default",
                 errorModel);
    // E1.5.2 modes (the default mode "link" ignores these arguments)
    std::string mode = "link";
    std::string linksFile;
    double gnbX = 8.5;
    double gnbY = 21.0;
    double gnbZ = 27.0;
    uint32_t gnbRows = 8;
    uint32_t gnbCols = 16;
    std::string gnbElement = "3gpp";
    uint32_t copies = 1;
    std::string gnbRole = "s";
    std::string gnbWeights = "dp";
    double bearingDeg = 30.0;
    cmd.AddValue("mode", "link (E1.5.1), chan or nrfading (E1.5.2)", mode);
    cmd.AddValue("linksFile", "E1.5.2 links CSV", linksFile);
    cmd.AddValue("gnbX", "gNB x [m] (E1.5.2)", gnbX);
    cmd.AddValue("gnbY", "gNB y [m] (E1.5.2)", gnbY);
    cmd.AddValue("gnbZ", "gNB z [m] (E1.5.2)", gnbZ);
    cmd.AddValue("gnbRows", "gNB array rows (vertical) (E1.5.2)", gnbRows);
    cmd.AddValue("gnbCols", "gNB array columns (horizontal) (E1.5.2)", gnbCols);
    cmd.AddValue("gnbElement", "3gpp or iso (mode chan)", gnbElement);
    cmd.AddValue("copies", "co-located UE copies per link (mode chan)", copies);
    cmd.AddValue("gnbRole", "s: gNB transmits first (DL); u: UE transmits first (mode chan)", gnbRole);
    cmd.AddValue("gnbWeights", "dp: NR DirectPath vector; dpconj: its conjugate (mode chan)", gnbWeights);
    cmd.AddValue("bearingDeg", "gNB sector bearing [deg] (mode nrfading)", bearingDeg);
    // E1.5.2-1 (modes beams and nrfading)
    std::string gnbBeam = "dp";
    uint32_t oversampling = 4;
    uint32_t dumpCopies = 1;
    cmd.AddValue("gnbBeam", "dp (NR DirectPath), cb (DFT codebook) or eig (eigen beam) (mode nrfading)", gnbBeam);
    cmd.AddValue("oversampling", "DFT codebook oversampling per dimension (modes beams, nrfading)", oversampling);
    cmd.AddValue("dumpCopies", "copies per link whose per-RB channel is dumped (mode beams)", dumpCopies);
    // E1.5.4-0 (mode nrfading)
    bool ofdmaDiag = false;
    cmd.AddValue("ofdmaDiag", "write DL DCI, slot-level TB and per-RB cross-beam gain files (mode nrfading)", ofdmaDiag);
    cmd.Parse(argc, argv);
    if (mode == "beams")
    {
        const double scs = 15e3 * std::pow(2.0, numerology);
        const auto numRbs = static_cast<uint32_t>(std::floor(bandwidth * (1 - rbOverhead) / (12 * scs)));
        return RunBeamsMode(linksFile,
                            frequency,
                            numRbs,
                            scs,
                            Vector(gnbX, gnbY, gnbZ),
                            gnbRows,
                            gnbCols,
                            copies,
                            oversampling,
                            dumpCopies);
    }
    if (mode == "chan")
    {
        const double scs = 15e3 * std::pow(2.0, numerology);
        const auto numRbs = static_cast<uint32_t>(std::floor(bandwidth * (1 - rbOverhead) / (12 * scs)));
        return RunChannelMode(linksFile,
                              frequency,
                              numRbs,
                              scs,
                              Vector(gnbX, gnbY, gnbZ),
                              gnbRows,
                              gnbCols,
                              gnbElement,
                              copies,
                              gnbRole,
                              gnbWeights);
    }
    if (mode == "nrfading")
    {
        return RunNrFadingMode(linksFile,
                               frequency,
                               bandwidth,
                               numerology,
                               rbOverhead,
                               gnbTxPower,
                               nfGnb,
                               nfUe,
                               Vector(gnbX, gnbY, gnbZ),
                               gnbRows,
                               gnbCols,
                               bearingDeg,
                               scheduler,
                               dlRateBps,
                               packetSize,
                               appStart,
                               simTime,
                               tddPattern,
                               gnbBeam,
                               oversampling,
                               ofdmaDiag);
    }
    NS_ABORT_MSG_IF(mode != "link", "unknown mode " << mode);
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
    if (!errorModel.empty())
    {
        // E1.5.3: NR LDPC error model for AMC (gNB DL/UL, and the UE's DL AMC, which NR
        // creates from the gNB DL AMC factory) and for decoding (both spectrum PHYs). The
        // NR default is NrLteMiErrorModel.
        nrHelper->SetDlErrorModel(errorModel);
        nrHelper->SetUlErrorModel(errorModel);
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
        if (!errorModel.empty())
        {
            auto sched = DynamicCast<NrMacSchedulerNs3>(
                DynamicCast<NrGnbNetDevice>(gnbDevs.Get(0))->GetScheduler(0));
            NS_ABORT_MSG_IF(!sched, "scheduler is not an NrMacSchedulerNs3");
            info << "error_model_requested," << errorModel << '\n'
                 << "error_model_gnb_dl_amc," << sched->GetDlAmc()->GetErrorModelType().GetName() << '\n'
                 << "error_model_gnb_ul_amc," << sched->GetUlAmc()->GetErrorModelType().GetName() << '\n';
        }
    }

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    if (!errorModel.empty())
    {
        // E1.5.3: type of the error-model INSTANCES used for decoding (created at the first TB)
        std::ofstream em("e153_error_model.csv");
        em << "device,ue_index,error_model_instance\n";
        auto name = [](Ptr<NrErrorModel> m) {
            return m ? m->GetInstanceTypeId().GetName() : std::string("none");
        };
        em << "gnb,-1," << name(NrHelper::GetGnbPhy(gnbDevs.Get(0), 0)->GetSpectrumPhy()->GetErrorModel())
           << '\n';
        for (uint32_t i = 0; i < nUe; ++i)
        {
            em << "ue," << i << ','
               << name(NrHelper::GetUePhy(ueDevs.Get(i), 0)->GetSpectrumPhy()->GetErrorModel()) << '\n';
        }
    }

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
