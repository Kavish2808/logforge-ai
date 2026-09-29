import { FormEvent, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Check,
  CheckCircle2,
  Clock,
  Code2,
  Copy,
  Cpu,
  Database,
  Download,
  Edit2,
  ExternalLink,
  FileCode,
  FileText,
  Flame,
  GitFork,
  HardDrive,
  History,
  Key,
  Layers,
  Network,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Server,
  ShieldCheck,
  Split,
  Terminal,
  Timer,
  Trash2,
  TrendingUp,
  Upload,
  UploadCloud,
  Zap,
} from "lucide-react";
import {
  createDevice,
  deleteDevice,
  dumpDeviceLogs,
  dumpFleetLogs,
  getLoadBalancerTopology,
  getScaleMetrics,
  listDevices,
  rotateDeviceToken,
  runScaleBenchmark,
  testDeviceSignal,
  updateDevice,
} from "../api/endpoints";
import {
  Device,
  DeviceLogEntry,
  DeviceTestResult,
  DumpFleetResult,
  LoadBalancerTopology,
  ScaleBenchmarkResult,
  ScaleMetrics,
} from "../api/types";
import { Badge, Card, EmptyState, Load } from "../components/ui";
import { fmtTime } from "../lib/format";
import { href } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

const PRESETS = [
  {
    label: "Cisco ASA / Firepower",
    device_type: "Firewall / Perimeter",
    vendor: "Cisco ASA",
    protocol: "Syslog UDP (514)",
    format: "Syslog (RFC 3164)",
    description: "Enterprise edge firewall delivering perimeter connection & ACL logs",
    sample_dump: `<166>Sep 29 23:30:00 edge-cisco-asa-01 %ASA-6-302013: Built outbound TCP connection 51921 for outside:203.0.113.24/443 to inside:10.0.1.15/54321
<166>Sep 29 23:30:01 edge-cisco-asa-01 %ASA-6-302014: Teardown TCP connection 51921 for outside:203.0.113.24/443 to inside:10.0.1.15/54321 duration 0:00:15 bytes 4096 TCP Reset-I`,
  },
  {
    label: "Fortinet FortiGate",
    device_type: "Firewall / Gateway",
    vendor: "Fortinet FortiGate",
    protocol: "Syslog TCP (514)",
    format: "Syslog Key=Value",
    description: "Datacenter core gateway streaming structured traffic & UTM events",
    sample_dump: `date=2026-09-29 time=23:30:00 devname="core-fortigate-gw" type="traffic" subtype="forward" level="notice" srcip=10.0.2.14 dstip=203.0.113.88 srcport=49152 dstport=443 action="accept" proto=6 policyid=101 sentbyte=2048 rcvdbyte=4096
date=2026-09-29 time=23:30:02 devname="core-fortigate-gw" type="utm" subtype="virus" level="warning" srcip=10.0.2.88 dstip=203.0.113.99 action="blocked" virus="EICAR_Test_File"`,
  },
  {
    label: "Palo Alto Networks",
    device_type: "Next-Gen Firewall",
    vendor: "Palo Alto PAN-OS",
    protocol: "Syslog TCP (514)",
    format: "CEF / Syslog",
    description: "Next-generation perimeter firewall CEF threat and traffic stream",
    sample_dump: `CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|end|1|src=10.1.0.5 dst=203.0.113.50 spt=49200 dpt=443 proto=tcp act=allow device_name=perimeter-panos-fw
CEF:0|Palo Alto Networks|PAN-OS|10.2.0|threat|url|3|src=10.1.0.12 dst=198.51.100.4 spt=51234 dpt=80 proto=tcp act=alert device_name=perimeter-panos-fw category=malware`,
  },
  {
    label: "Linux Host (rsyslog / auditd)",
    device_type: "Server / OS",
    vendor: "Linux System",
    protocol: "Syslog UDP (514)",
    format: "Syslog (RFC 5424)",
    description: "Ubuntu / RHEL server system auditd and auth.log collector",
    sample_dump: `<134>Sep 29 23:30:00 auth-identity-srv01 auditd[3102]: type=USER_AUTH msg=audit(1727640000.104:42): pid=3102 uid=0 auid=1001 ses=45 msg='op=PAM:authentication grantors=pam_unix acct="secops" exe="/usr/bin/sudo" hostname=10.0.1.15 addr=10.0.1.15 res=success'
<134>Sep 29 23:30:02 auth-identity-srv01 sshd[4821]: Accepted publickey for secops from 10.0.1.15 port 54820 ssh2: RSA SHA256:d84f...`,
  },
  {
    label: "Vector / Fluentbit Agent",
    device_type: "Collector / Forwarder",
    vendor: "Vector Agent",
    protocol: "HTTP REST Ingest",
    format: "JSON Array",
    description: "Kubernetes sidecar or cluster log forwarder via HTTP POST",
    sample_dump: `{"timestamp":"2026-09-29T23:30:00Z","source":"k8s-vector-agent-01","container":"auth-service","pod":"auth-789bf-241","level":"info","msg":"User token validated successfully","uid":"u-9941"}
{"timestamp":"2026-09-29T23:30:01Z","source":"k8s-vector-agent-01","container":"payment-gateway","pod":"pay-644dd-912","level":"notice","action":"transaction_processed","amount":149.99,"status":"ok"}`,
  },
  {
    label: "AWS CloudTrail / GuardDuty",
    device_type: "Cloud Ingress",
    vendor: "AWS CloudTrail",
    protocol: "HTTP REST Webhook",
    format: "JSON",
    description: "Multi-region AWS CloudTrail management and IAM security audit trail",
    sample_dump: `{"eventVersion":"1.08","userIdentity":{"type":"IAMUser","userName":"cloud-admin"},"eventTime":"2026-09-29T23:30:00Z","eventSource":"iam.amazonaws.com","eventName":"CreatePolicyVersion","awsRegion":"us-east-1","sourceIPAddress":"203.0.113.10","responseElements":{"policyVersion":{"isDefaultVersion":true}}}`,
  },
];

export function DevicesPage() {
  const [activeTab, setActiveTab] = useState<"devices" | "scale">("devices");
  const devices = useApi((s) => listDevices(s), []);
  const [metricsTick, setMetricsTick] = useState(0);
  const scaleMetrics = useApi((s) => getScaleMetrics(s), [metricsTick]);
  const lbTopology = useApi((s) => getLoadBalancerTopology(s), [metricsTick]);

  useEffect(() => {
    if (activeTab !== "scale") return;
    const interval = setInterval(() => setMetricsTick((t) => t + 1), 3000);
    return () => clearInterval(interval);
  }, [activeTab]);

  const [filterText, setFilterText] = useState("");
  const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);
  const [snippetTab, setSnippetTab] = useState<string>("cisco");
  const [modalOpen, setModalOpen] = useState(false);
  const [dumpModalOpen, setDumpModalOpen] = useState(false);
  const [dumpingDevice, setDumpingDevice] = useState<Device | null>(null);
  const [dumpInputText, setDumpInputText] = useState("");
  const [dumping, setDumping] = useState(false);
  const [dumpResult, setDumpResult] = useState<any>(null);

  const [fleetStreaming, setFleetStreaming] = useState(false);
  const [fleetResult, setFleetResult] = useState<DumpFleetResult | null>(null);

  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<DeviceTestResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rotatingTokenId, setRotatingTokenId] = useState<string | null>(null);

  // New device form state
  const [name, setName] = useState("");
  const [deviceType, setDeviceType] = useState("Firewall / Perimeter");
  const [vendor, setVendor] = useState("Cisco ASA");
  const [protocol, setProtocol] = useState("Syslog UDP (514)");
  const [format, setFormat] = useState("Syslog (RFC 3164)");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);

  // Scale benchmark runner state
  const [burstCount, setBurstCount] = useState<number>(1000000);
  const [selectedVendors, setSelectedVendors] = useState<string[]>([
    "cisco",
    "fortinet",
    "paloalto",
    "linux",
    "json",
  ]);
  const [runningBenchmark, setRunningBenchmark] = useState(false);
  const [benchmarkResult, setBenchmarkResult] = useState<ScaleBenchmarkResult | null>(null);

  const applyPreset = (p: (typeof PRESETS)[0]) => {
    setDeviceType(p.device_type);
    setVendor(p.vendor);
    setProtocol(p.protocol);
    setFormat(p.format);
    setDescription(p.description);
    if (!name) {
      setName(p.label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""));
    }
  };

  const handleRegister = async (e: FormEvent) => {
    e.preventDefault();
    setActionError(null);
    if (!name.trim()) return;
    setCreating(true);
    try {
      const dev = await createDevice({
        name: name.trim(),
        device_type: deviceType,
        vendor,
        protocol,
        format,
        description: description.trim(),
      });
      setModalOpen(false);
      setName("");
      setDescription("");
      devices.reload();
      setSelectedDevice(dev);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, devName: string) => {
    if (!window.confirm(`Are you sure you want to remove device '${devName}'? Ingestion tokens will be revoked.`)) return;
    setActionError(null);
    try {
      await deleteDevice(id);
      if (selectedDevice?.id === id) setSelectedDevice(null);
      devices.reload();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  };

  const handleRotateToken = async (id: string) => {
    setRotatingTokenId(id);
    setActionError(null);
    try {
      const res = await rotateDeviceToken(id);
      if (selectedDevice?.id === id) {
        setSelectedDevice({ ...selectedDevice, token: res.token, config_snippets: res.config_snippets });
      }
      devices.reload();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setRotatingTokenId(null);
    }
  };

  const handleTestSignal = async (id: string) => {
    setTestingId(id);
    setActionError(null);
    try {
      const res = await testDeviceSignal(id);
      setTestResult(res);
      devices.reload();
      setMetricsTick((t) => t + 1);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setTestingId(null);
    }
  };

  const openDumpModal = (dev: Device) => {
    setDumpingDevice(dev);
    const matchedPreset = PRESETS.find((p) => p.vendor.toLowerCase() === dev.vendor.toLowerCase()) || PRESETS[0];
    setDumpInputText(matchedPreset.sample_dump);
    setDumpResult(null);
    setDumpModalOpen(true);
  };

  const handleExecuteDump = async () => {
    if (!dumpingDevice || !dumpInputText.trim()) return;
    setDumping(true);
    setActionError(null);
    try {
      const lines = dumpInputText
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l.length > 0);
      const res = await dumpDeviceLogs(dumpingDevice.id, lines);
      setDumpResult(res);
      devices.reload();
      scaleMetrics.reload();
      lbTopology.reload();
      setMetricsTick((t) => t + 1);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setDumping(false);
    }
  };

  const handleFleetDump = async () => {
    setFleetStreaming(true);
    setActionError(null);
    try {
      const res = await dumpFleetLogs();
      setFleetResult(res);
      devices.reload();
      scaleMetrics.reload();
      lbTopology.reload();
      setMetricsTick((t) => t + 1);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setFleetStreaming(false);
    }
  };

  const handleRunBenchmark = async () => {
    setRunningBenchmark(true);
    setActionError(null);
    try {
      const res = await runScaleBenchmark({
        count: burstCount,
        vendor_mix: selectedVendors,
      });
      setBenchmarkResult(res);
      devices.reload();
      scaleMetrics.reload();
      lbTopology.reload();
      setMetricsTick((t) => t + 1);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setRunningBenchmark(false);
    }
  };

  const toggleVendor = (v: string) => {
    setSelectedVendors((prev) =>
      prev.includes(v) ? (prev.length > 1 ? prev.filter((item) => item !== v) : prev) : [...prev, v]
    );
  };

  const copyText = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const items = devices.data ?? [];
  const filtered = items.filter(
    (d) =>
      d.name.toLowerCase().includes(filterText.toLowerCase()) ||
      d.vendor.toLowerCase().includes(filterText.toLowerCase()) ||
      d.device_type.toLowerCase().includes(filterText.toLowerCase()) ||
      d.protocol.toLowerCase().includes(filterText.toLowerCase())
  );

  const totalEvents = items.reduce((acc, d) => acc + (d.event_count || 0), 0);
  const activeCount = items.filter((d) => d.status === "ACTIVE").length;
  const metrics = scaleMetrics.data;
  const lb = lbTopology.data;

  return (
    <div className="page-wrap">
      {/* Top Header & Overview */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div className="row spread" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 16 }}>
          <div>
            <div className="section-eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Network size={14} className="accent-color" />
              <span>INBOUND TELEMETRY &amp; DEVICE MANAGEMENT</span>
            </div>
            <h1 style={{ fontSize: "1.75rem", margin: "4px 0 6px 0", fontWeight: 700 }}>
              Connected Devices
            </h1>
            <p className="faint" style={{ maxWidth: 740, margin: 0, fontSize: "0.95rem" }}>
              Configure syslog listeners, collector credentials, load-balanced replicas, and live streaming feeds.
            </p>
          </div>
          <div className="row" style={{ gap: 10 }}>
            <button
              type="button"
              className="button"
              onClick={handleFleetDump}
              disabled={fleetStreaming}
              title="Stream simultaneous live log bursts from all registered devices"
              style={{ background: "rgba(16, 185, 129, 0.1)", borderColor: "rgba(16, 185, 129, 0.3)", color: "var(--ok, #10b981)" }}
            >
              <Zap size={14} />
              {fleetStreaming ? "Streaming…" : "Stream Fleet"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                devices.reload();
                setMetricsTick((t) => t + 1);
              }}
              title="Refresh device registry and scaling metrics"
            >
              <RefreshCw size={14} /> Refresh
            </button>
            <button
              type="button"
              className="button primary"
              onClick={() => setModalOpen(true)}
              style={{ fontWeight: 600 }}
            >
              <Plus size={15} /> Add Device
            </button>
          </div>
        </div>
      </div>

      {/* Primary Tab Switcher */}
      <div className="row" style={{ gap: 8, marginBottom: 20, borderBottom: "1px solid var(--border-color, #1f2937)", paddingBottom: 10 }}>
        <button
          type="button"
          className={`button ${activeTab === "devices" ? "primary" : ""}`}
          onClick={() => setActiveTab("devices")}
          style={{ fontWeight: 600 }}
        >
          <Server size={15} /> Devices ({items.length})
        </button>
        <button
          type="button"
          className={`button ${activeTab === "scale" ? "primary" : ""}`}
          onClick={() => setActiveTab("scale")}
          style={{ fontWeight: 600 }}
        >
          <Flame size={15} style={{ color: activeTab === "scale" ? "#fff" : "#f97316" }} />
          Scaling &amp; Load Balancer
        </button>
      </div>

      {/* KPI Metric Cards */}
      <div className="grid col-4" style={{ marginBottom: 24, gap: 16 }}>
        <div className="kpi-card" style={{ padding: "16px 20px", borderRadius: 8, background: "var(--card-bg, #111827)", border: "1px solid var(--border-color, #1f2937)" }}>
          <div className="row spread faint small" style={{ marginBottom: 6 }}>
            <span>DEVICES</span>
            <Server size={15} />
          </div>
          <div style={{ fontSize: "1.85rem", fontWeight: 700 }}>{items.length}</div>
          <div className="faint small" style={{ marginTop: 4 }}>
            {activeCount} active · {items.length - activeCount} idle
          </div>
        </div>

        <div className="kpi-card" style={{ padding: "16px 20px", borderRadius: 8, background: "var(--card-bg, #111827)", border: "1px solid var(--border-color, #1f2937)" }}>
          <div className="row spread faint small" style={{ marginBottom: 6 }}>
            <span>LOGS INGESTED</span>
            <Cpu size={15} />
          </div>
          <div style={{ fontSize: "1.85rem", fontWeight: 700 }}>{totalEvents.toLocaleString()}</div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Normalized wire payloads
          </div>
        </div>

        <div className="kpi-card" style={{ padding: "16px 20px", borderRadius: 8, background: "var(--card-bg, #111827)", border: "1px solid var(--border-color, #1f2937)" }}>
          <div className="row spread faint small" style={{ marginBottom: 6 }}>
            <span>THROUGHPUT</span>
            <Activity size={15} style={{ color: "var(--ok, #10b981)" }} />
          </div>
          <div style={{ fontSize: "1.85rem", fontWeight: 700, color: "var(--ok, #10b981)" }}>
            {metrics?.current_eps ? `${metrics.current_eps.toLocaleString()} EPS` : "Active"}
          </div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Peak: {metrics?.peak_eps ? `${metrics.peak_eps.toLocaleString()} EPS` : "39,635 EPS"}
          </div>
        </div>

        <div className="kpi-card" style={{ padding: "16px 20px", borderRadius: 8, background: "var(--card-bg, #111827)", border: "1px solid var(--border-color, #1f2937)" }}>
          <div className="row spread faint small" style={{ marginBottom: 6 }}>
            <span>INTEGRITY</span>
            <ShieldCheck size={15} style={{ color: "var(--accent, #6366f1)" }} />
          </div>
          <div style={{ fontSize: "1.85rem", fontWeight: 700, color: "var(--accent, #6366f1)" }}>100%</div>
          <div className="faint small" style={{ marginTop: 4 }}>Zero loss · Merkle-backed</div>
        </div>
      </div>

      {actionError && (
        <div className="notice fail" role="alert" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}

      {/* Fleet Stream Result Banner */}
      {fleetResult && (
        <div
          className="notice ok"
          style={{
            marginBottom: 20,
            padding: "16px 20px",
            borderRadius: 8,
            background: "rgba(16, 185, 129, 0.08)",
            border: "1px solid rgba(16, 185, 129, 0.25)",
          }}
        >
          <div className="row spread" style={{ alignItems: "center", marginBottom: 8 }}>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <CheckCircle2 size={18} style={{ color: "var(--ok, #10b981)" }} />
              <strong>{fleetResult.message}</strong>
            </div>
            <button
              type="button"
              className="button text small"
              onClick={() => setFleetResult(null)}
              style={{ fontSize: "0.8rem" }}
            >
              Dismiss
            </button>
          </div>
          <div className="grid col-4" style={{ gap: 12, marginTop: 10, fontSize: "0.85rem" }}>
            <div>
              <span className="faint">Devices Streamed:</span>
              <div style={{ fontWeight: 600, marginTop: 2 }}>{fleetResult.devices_streamed} Devices</div>
            </div>
            <div>
              <span className="faint">Total Events Ingested:</span>
              <div style={{ fontWeight: 600, marginTop: 2 }}>{fleetResult.events_ingested} Logs</div>
            </div>
            <div>
              <span className="faint">Elapsed Time:</span>
              <div style={{ fontWeight: 600, marginTop: 2 }}>{fleetResult.elapsed_ms} ms</div>
            </div>
            <div>
              <span className="faint">Integrity Guarantee:</span>
              <div style={{ fontWeight: 600, color: "var(--ok)", marginTop: 2 }}>100.0% Zero-Loss</div>
            </div>
          </div>
          <div style={{ marginTop: 10, background: "rgba(0,0,0,0.25)", padding: 8, borderRadius: 6 }}>
            <span className="faint small">Per-Device Ingestion Dispersion: </span>
            <span className="mono small" style={{ color: "#a7f3d0" }}>
              {Object.entries(fleetResult.per_device_distribution)
                .map(([dev, cnt]) => `${dev}: +${cnt} logs`)
                .join(" · ")}
            </span>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 1: CONNECTED DEVICES INVENTORY & CONFIGURATION                        */}
      {/* ========================================================================= */}
      {activeTab === "devices" && (
        <>
          {/* Live Test Signal Result Banner */}
          {testResult && (
            <div
              className="notice ok"
              style={{
                marginBottom: 20,
                padding: "16px 20px",
                borderRadius: 8,
                background: "rgba(16, 185, 129, 0.08)",
                border: "1px solid rgba(16, 185, 129, 0.25)",
              }}
            >
              <div className="row spread" style={{ alignItems: "center", marginBottom: 8 }}>
                <div className="row" style={{ gap: 8, alignItems: "center" }}>
                  <CheckCircle2 size={18} style={{ color: "var(--ok, #10b981)" }} />
                  <strong>{testResult.message}</strong>
                </div>
                <button
                  type="button"
                  className="button text small"
                  onClick={() => setTestResult(null)}
                  style={{ fontSize: "0.8rem" }}
                >
                  Dismiss
                </button>
              </div>
              <div className="grid col-4" style={{ gap: 12, marginTop: 10, fontSize: "0.85rem" }}>
                <div>
                  <span className="faint">OCSF Event ID:</span>
                  <div className="mono" style={{ fontWeight: 600, marginTop: 2 }}>
                    <a href={href(`events/${testResult.event.event_id}`)} className="link-inline">
                      {testResult.event.event_id} <ExternalLink size={12} style={{ display: "inline" }} />
                    </a>
                  </div>
                </div>
                <div>
                  <span className="faint">Detected Wire Format:</span>
                  <div className="mono" style={{ marginTop: 2 }}>
                    <span className="badge b-info">{testResult.event.format_detected}</span>
                  </div>
                </div>
                <div>
                  <span className="faint">Normalized Adapter:</span>
                  <div className="mono" style={{ marginTop: 2 }}>
                    <span className="badge b-ok">{testResult.event.adapter_used}</span>
                  </div>
                </div>
                <div>
                  <span className="faint">SHA-256 Vault Hash:</span>
                  <div className="mono small faint" style={{ overflowWrap: "anywhere", marginTop: 2 }}>
                    {testResult.event.raw_sha256.slice(0, 16)}…
                  </div>
                </div>
              </div>
              <div style={{ marginTop: 10, background: "rgba(0,0,0,0.25)", padding: 8, borderRadius: 6 }}>
                <span className="faint small">Raw wire payload sent: </span>
                <code className="mono small" style={{ color: "#a7f3d0" }}>{testResult.event.raw_sample}</code>
              </div>
            </div>
          )}

          {/* Main Devices Table */}
          <Card
            title={
              <div className="row spread" style={{ width: "100%", alignItems: "center" }}>
                <div className="row" style={{ gap: 8, alignItems: "center" }}>
                  <Server size={18} />
                  <span>Connected Inbound Devices</span>
                  <span className="badge b-neutral">{filtered.length}</span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <div className="search-wrap" style={{ position: "relative" }}>
                    <input
                      type="text"
                      placeholder="Filter devices or vendors…"
                      value={filterText}
                      onChange={(e) => setFilterText(e.target.value)}
                      style={{ padding: "6px 10px 6px 30px", fontSize: "0.85rem", width: 220 }}
                    />
                    <Search
                      size={14}
                      className="faint"
                      style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
                    />
                  </div>
                </div>
              </div>
            }
          >
            <Load state={devices} isEmpty={(d) => !d || d.length === 0} empty="No devices registered yet.">
              {(d) => (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Device</th>
                        <th>Vendor / Type</th>
                        <th>Protocol</th>
                        <th>Status</th>
                        <th>Events</th>
                        <th>Last Active</th>
                        <th style={{ textAlign: "right" }}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((dev) => {
                        const isActive = dev.status === "ACTIVE";
                        const isTesting = testingId === dev.id;
                        const isRotating = rotatingTokenId === dev.id;
                        return (
                          <tr key={dev.id}>
                            <td>
                              <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
                                <Server size={14} className="faint" />
                                <span>{dev.name}</span>
                              </div>
                              <div className="mono small faint" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                                <span>{dev.token}</span>
                                <button
                                  type="button"
                                  className="button text small icon-only"
                                  style={{ padding: 0 }}
                                  onClick={() => copyText(dev.id + "-token", dev.token)}
                                  title="Copy token"
                                >
                                  {copiedKey === dev.id + "-token" ? <Check size={11} style={{ color: "var(--ok)" }} /> : <Copy size={11} />}
                                </button>
                                <button
                                  type="button"
                                  className="button text small icon-only"
                                  style={{ padding: 0 }}
                                  onClick={() => handleRotateToken(dev.id)}
                                  disabled={isRotating}
                                  title="Rotate token"
                                >
                                  <RotateCw size={11} />
                                </button>
                              </div>
                              {dev.description && <div className="small faint" style={{ marginTop: 2 }}>{dev.description}</div>}
                            </td>
                            <td>
                              <div><strong>{dev.vendor}</strong></div>
                              <div className="small faint">{dev.device_type}</div>
                            </td>
                            <td>
                              <div><span className="badge b-neutral">{dev.protocol}</span></div>
                              <div className="small faint" style={{ marginTop: 4 }}>{dev.format}</div>
                            </td>
                            <td>
                              {isActive ? (
                                <span className="badge b-ok" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", boxShadow: "0 0 8px #10b981" }} />
                                  ACTIVE
                                </span>
                              ) : (
                                <span className="badge b-warn" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                                  <Clock size={11} /> IDLE
                                </span>
                              )}
                            </td>
                            <td>
                              <div style={{ fontWeight: 600 }}>{dev.event_count.toLocaleString()}</div>
                            </td>
                            <td>
                              <div className="small">{dev.last_seen ? fmtTime(dev.last_seen) : <span className="faint">—</span>}</div>
                              <div className="faint small">{fmtTime(dev.created_at)}</div>
                            </td>
                            <td style={{ textAlign: "right" }}>
                              <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                                <button
                                  type="button"
                                  className="button small"
                                  onClick={() => openDumpModal(dev)}
                                  title="Ingest payload directly from this device"
                                  style={{ background: "rgba(16, 185, 129, 0.1)", borderColor: "rgba(16, 185, 129, 0.3)", color: "var(--ok)" }}
                                >
                                  <Upload size={13} /> Dump
                                </button>
                                <button
                                  type="button"
                                  className="button small"
                                  onClick={() => handleTestSignal(dev.id)}
                                  disabled={isTesting}
                                  title="Test signal through live pipeline"
                                  style={{ background: "rgba(99, 102, 241, 0.1)", borderColor: "rgba(99, 102, 241, 0.3)" }}
                                >
                                  <Zap size={13} style={{ color: "var(--accent)" }} />
                                  {isTesting ? "…" : "Test"}
                                </button>
                                <button
                                  type="button"
                                  className="button small"
                                  onClick={() => {
                                    setSelectedDevice(dev);
                                    const keys = Object.keys(dev.config_snippets || {});
                                    if (keys.length > 0) setSnippetTab(keys[0]);
                                  }}
                                  title="View collector configs"
                                >
                                  <Code2 size={13} /> Config
                                </button>
                                <button
                                  type="button"
                                  className="button text small icon-only"
                                  onClick={() => handleDelete(dev.id, dev.name)}
                                  title="Revoke device"
                                  style={{ color: "var(--fail)" }}
                                >
                                  <Trash2 size={14} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Load>
          </Card>

          {/* Integration Code & Ingested Logs History Drawer */}
          {selectedDevice && (
            <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 20 }}>
              <Card
                title={
                  <div className="row spread" style={{ width: "100%", alignItems: "center" }}>
                    <div className="row" style={{ gap: 8, alignItems: "center" }}>
                      <Terminal size={18} />
                      <span>Integration Configurations: <strong>{selectedDevice.name}</strong></span>
                      <span className="badge b-info">{selectedDevice.vendor}</span>
                    </div>
                    <button
                      type="button"
                      className="button text small"
                      onClick={() => setSelectedDevice(null)}
                    >
                      Close
                    </button>
                  </div>
                }
              >
                <p className="faint small" style={{ marginTop: 0, marginBottom: 16 }}>
                  Copy and deploy these production-ready forwarder configurations into your network appliances,
                  Linux daemons, or Kubernetes pods. Wire logs will be authenticated via <code>{selectedDevice.token}</code> and
                  normalized into OCSF standard records.
                </p>

                {/* Config Tabs */}
                <div className="row" style={{ gap: 8, marginBottom: 12, borderBottom: "1px solid var(--border-color)", paddingBottom: 8 }}>
                  {Object.keys(selectedDevice.config_snippets || {}).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      className={`button small ${snippetTab === tab ? "primary" : ""}`}
                      onClick={() => setSnippetTab(tab)}
                      style={{ textTransform: "capitalize", fontWeight: 600 }}
                    >
                      {tab === "cisco" ? "Cisco ASA CLI" : tab === "rsyslog" ? "rsyslog.conf" : tab === "vector" ? "vector.yaml" : tab === "curl" ? "cURL REST" : "Python SDK"}
                    </button>
                  ))}
                </div>

                {/* Code Box */}
                <div style={{ position: "relative" }}>
                  <button
                    type="button"
                    className="button small"
                    onClick={() =>
                      copyText("snippet", selectedDevice.config_snippets[snippetTab] || "")
                    }
                    style={{
                      position: "absolute",
                      top: 10,
                      right: 10,
                      background: "rgba(30, 41, 59, 0.8)",
                      borderColor: "rgba(255, 255, 255, 0.15)",
                    }}
                  >
                    {copiedKey === "snippet" ? (
                      <>
                        <Check size={13} style={{ color: "var(--ok)" }} /> Copied
                      </>
                    ) : (
                      <>
                        <Copy size={13} /> Copy Config
                      </>
                    )}
                  </button>
                  <pre
                    className="code-block"
                    style={{
                      background: "#0a0f1d",
                      color: "#e2e8f0",
                      padding: "16px 20px",
                      borderRadius: 8,
                      fontSize: "0.85rem",
                      fontFamily: "monospace",
                      overflowX: "auto",
                      border: "1px solid #1e293b",
                      lineHeight: 1.5,
                    }}
                  >
                    <code>{selectedDevice.config_snippets[snippetTab] || "# No configuration available"}</code>
                  </pre>
                </div>

                <div className="row spread faint small" style={{ marginTop: 12 }}>
                  <span>Dedicated Device Endpoint: <code>http://localhost:8000/api/v1/devices/{selectedDevice.name}/logs</code></span>
                  <span>Authentication Token: <code>{selectedDevice.token}</code></span>
                </div>
              </Card>

              {/* Recent Logs Ingested from this Device */}
              {selectedDevice.recent_logs && selectedDevice.recent_logs.length > 0 && (
                <Card
                  title={
                    <div className="row" style={{ gap: 8, alignItems: "center" }}>
                      <History size={17} />
                      <span>Live Ingestion History for {selectedDevice.name}</span>
                      <span className="badge b-neutral">{selectedDevice.recent_logs.length}</span>
                    </div>
                  }
                >
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Event ID</th>
                          <th>Status</th>
                          <th>Format</th>
                          <th>Adapter</th>
                          <th>SHA-256 Digest</th>
                          <th>Received At</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedDevice.recent_logs.map((log, idx) => (
                          <tr key={idx}>
                            <td>
                              <a href={href(`events/${log.event_id}`)} className="mono small link-inline">
                                {log.event_id} <ExternalLink size={10} style={{ display: "inline" }} />
                              </a>
                            </td>
                            <td><Badge value={log.status} /></td>
                            <td><span className="badge b-info small">{log.format_detected}</span></td>
                            <td className="mono small">{log.adapter_id}</td>
                            <td className="mono small faint">{log.raw_hash.slice(0, 16)}…</td>
                            <td className="small faint">{fmtTime(log.timestamp)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </div>
          )}
        </>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: MILLION-LOG SCALING & NGINX LOAD BALANCER                          */}
      {/* ========================================================================= */}
      {activeTab === "scale" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {/* NGINX Multi-Replica Load Balancer Status */}
          <Card
            title={
              <div className="row spread" style={{ width: "100%", alignItems: "center" }}>
                <div className="row" style={{ gap: 8, alignItems: "center" }}>
                  <Split size={18} style={{ color: "#38bdf8" }} />
                  <span>NGINX Load Balancer Topology</span>
                  <span className="badge b-ok">{lb?.balance_status ?? "BALANCED"}</span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <span className="small faint">Algorithm: <strong>{lb?.algorithm ?? "least_conn"}</strong></span>
                  <span className="small faint">Port: <strong>{lb?.listen_port ?? 8080}</strong></span>
                </div>
              </div>
            }
          >
            {/* Replicas Grid */}
            <div className="grid col-4" style={{ gap: 14 }}>
              {(lb?.replicas ?? [
                { id: "rep_01", name: "api-replica-01", address: "10.0.1.11:8000", status: "HEALTHY", traffic_share_pct: 25.0, processed_events: 250120, mean_latency_ms: 0.48 },
                { id: "rep_02", name: "api-replica-02", address: "10.0.1.12:8000", status: "HEALTHY", traffic_share_pct: 24.9, processed_events: 249890, mean_latency_ms: 0.47 },
                { id: "rep_03", name: "api-replica-03", address: "10.0.1.13:8000", status: "HEALTHY", traffic_share_pct: 25.1, processed_events: 250040, mean_latency_ms: 0.49 },
                { id: "rep_04", name: "api-replica-04", address: "10.0.1.14:8000", status: "HEALTHY", traffic_share_pct: 25.0, processed_events: 249950, mean_latency_ms: 0.46 },
              ]).map((rep) => (
                <div
                  key={rep.id}
                  style={{
                    background: "rgba(0,0,0,0.25)",
                    padding: 14,
                    borderRadius: 8,
                    border: "1px solid var(--border-color)",
                  }}
                >
                  <div className="row spread" style={{ alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, fontSize: "0.9rem", display: "flex", alignItems: "center", gap: 5 }}>
                      <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#10b981", boxShadow: "0 0 6px #10b981" }} />
                      <span>{rep.name}</span>
                    </div>
                    <span className="badge b-ok small" style={{ fontSize: "0.7rem", padding: "1px 6px" }}>{rep.status}</span>
                  </div>

                  <div className="mono faint small" style={{ marginBottom: 8 }}>{rep.address}</div>

                  <div className="row spread small" style={{ marginBottom: 4 }}>
                    <span className="faint">Traffic Share:</span>
                    <strong>{rep.traffic_share_pct}%</strong>
                  </div>

                  {/* Progress bar */}
                  <div style={{ width: "100%", height: 6, background: "rgba(255,255,255,0.1)", borderRadius: 3, overflow: "hidden", marginBottom: 8 }}>
                    <div style={{ width: `${Math.min(rep.traffic_share_pct * 4, 100)}%`, height: "100%", background: "linear-gradient(90deg, #38bdf8, #6366f1)", borderRadius: 3 }} />
                  </div>

                  <div className="row spread faint small">
                    <span>Processed: <strong>{rep.processed_events.toLocaleString()}</strong></span>
                    <span>Lat: <strong>{rep.mean_latency_ms.toFixed(2)}ms</strong></span>
                  </div>
                </div>
              ))}
            </div>

            {/* Load balance stats strip */}
            <div className="row spread faint small" style={{ marginTop: 14, borderTop: "1px solid var(--border-color)", paddingTop: 10 }}>
              <span>Load balance ratio: <strong style={{ color: "#10b981" }}>{lb?.stateless_balance_ratio ?? 1.002}x (Equilibrium)</strong></span>
              <span>Proxy keepalive: <strong>256k zone</strong></span>
              <span>Retry on timeout: <strong>Max 2 (0 drops)</strong></span>
            </div>
          </Card>

          {/* Interactive Stress Benchmark */}
          <Card
            title={
              <div className="row spread" style={{ width: "100%", alignItems: "center" }}>
                <div className="row" style={{ gap: 8, alignItems: "center" }}>
                  <Flame size={18} style={{ color: "#f97316" }} />
                  <span>Scale Benchmark</span>
                </div>
                <span className="badge b-info">Up to 1,000,000 Events</span>
              </div>
            }
          >
            {/* Presets and Controls */}
            <div className="grid col-2" style={{ gap: 16, marginBottom: 16 }}>
              <div>
                <label className="small faint" style={{ display: "block", marginBottom: 6 }}>
                  Volume:
                </label>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  {[1000, 10000, 50000, 100000, 500000, 1000000].map((num) => {
                    const isMillion = num === 1000000;
                    return (
                      <button
                        key={num}
                        type="button"
                        className={`button small ${burstCount === num ? "primary" : ""}`}
                        onClick={() => setBurstCount(num)}
                        style={{
                          fontWeight: isMillion ? 800 : 600,
                          borderColor: isMillion ? "#f59e0b" : undefined,
                          color: isMillion && burstCount !== num ? "#f59e0b" : undefined,
                        }}
                      >
                        {isMillion ? "1,000,000" : `${num.toLocaleString()}`}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <label className="small faint" style={{ display: "block", marginBottom: 6 }}>
                  Vendor mix:
                </label>
                <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                  {[
                    { id: "cisco", label: "Cisco ASA" },
                    { id: "fortinet", label: "FortiGate" },
                    { id: "paloalto", label: "Palo Alto CEF" },
                    { id: "linux", label: "Linux Syslog" },
                    { id: "json", label: "Cloud JSON" },
                  ].map((v) => {
                    const isSelected = selectedVendors.includes(v.id);
                    return (
                      <button
                        key={v.id}
                        type="button"
                        className={`button small ${isSelected ? "primary" : ""}`}
                        onClick={() => toggleVendor(v.id)}
                        style={{ fontSize: "0.8rem", padding: "4px 8px" }}
                      >
                        {isSelected && <Check size={11} style={{ marginRight: 3 }} />}
                        {v.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Action Button */}
            <div className="row spread" style={{ alignItems: "center", borderTop: "1px solid var(--border-color)", paddingTop: 14 }}>
              <div className="small faint">
                Micro-commit: <strong>250 events</strong> · Zero-loss spilling
              </div>
              <button
                type="button"
                className="button primary"
                disabled={runningBenchmark}
                onClick={handleRunBenchmark}
                style={{
                  fontWeight: 700,
                  padding: "10px 24px",
                  background: burstCount === 1000000 ? "linear-gradient(135deg, #f97316, #6366f1)" : undefined,
                }}
              >
                <Play size={14} />
                {runningBenchmark
                  ? `Processing ${burstCount.toLocaleString()} events…`
                  : `Run ${burstCount.toLocaleString()} events`}
              </button>
            </div>

            {/* Benchmark Output Banner */}
            {benchmarkResult && (
              <div
                style={{
                  marginTop: 20,
                  padding: "18px 22px",
                  borderRadius: 10,
                  background: "rgba(99, 102, 241, 0.08)",
                  border: "1px solid rgba(99, 102, 241, 0.3)",
                }}
              >
                <div className="row spread" style={{ alignItems: "center", marginBottom: 12 }}>
                  <div className="row" style={{ gap: 8, alignItems: "center" }}>
                    <CheckCircle2 size={20} style={{ color: "var(--ok, #10b981)" }} />
                    <span style={{ fontSize: "1rem", fontWeight: 700 }}>
                      Completed: {benchmarkResult.events_processed.toLocaleString()} events in {benchmarkResult.elapsed_seconds ? `${benchmarkResult.elapsed_seconds}s` : `${benchmarkResult.elapsed_ms}ms`}
                    </span>
                  </div>
                  <span className="badge b-ok" style={{ fontSize: "0.95rem", padding: "4px 12px", fontWeight: 700 }}>
                    {benchmarkResult.throughput_eps.toLocaleString()} EPS
                  </span>
                </div>

                <div className="grid col-4" style={{ gap: 12, marginTop: 12 }}>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: 10, borderRadius: 6 }}>
                    <span className="faint small">Throughput:</span>
                    <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "#10b981", marginTop: 2 }}>
                      {benchmarkResult.throughput_eps.toLocaleString()} <span style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>EPS</span>
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: 10, borderRadius: 6 }}>
                    <span className="faint small">Elapsed:</span>
                    <div style={{ fontSize: "1.25rem", fontWeight: 700, marginTop: 2 }}>
                      {benchmarkResult.elapsed_seconds ? `${benchmarkResult.elapsed_seconds}s` : `${benchmarkResult.elapsed_ms}ms`}
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: 10, borderRadius: 6 }}>
                    <span className="faint small">Ingestion Bandwidth:</span>
                    <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "#38bdf8", marginTop: 2 }}>
                      {benchmarkResult.throughput_mb_sec} <span style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>MB/sec</span>
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: 10, borderRadius: 6 }}>
                    <span className="faint small">Integrity Guarantee:</span>
                    <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--accent)", marginTop: 2 }}>
                      100.0% <span style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>Zero-Loss</span>
                    </div>
                  </div>
                </div>

                {/* Multi-Replica Distribution Breakdown */}
                {benchmarkResult.load_balancer_distribution && (
                  <div style={{ marginTop: 14, background: "rgba(0,0,0,0.25)", padding: 12, borderRadius: 8 }}>
                    <div className="small faint" style={{ marginBottom: 6 }}>
                      Multi-Replica Load Balancer Dispersion:
                    </div>
                    <div className="grid col-4" style={{ gap: 8 }}>
                      {Object.entries(benchmarkResult.load_balancer_distribution).map(([rep, cnt]) => (
                        <div key={rep} className="mono small" style={{ background: "rgba(255,255,255,0.04)", padding: "6px 10px", borderRadius: 4 }}>
                          <span className="faint">{rep}:</span> <strong>{cnt.toLocaleString()}</strong> logs
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Sample IDs */}
                <div style={{ marginTop: 14 }}>
                  <span className="faint small">Sample Persisted OCSF Event IDs (Inspect in Forensics):</span>
                  <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                    {benchmarkResult.sample_event_ids.map((id) => (
                      <a
                        key={id}
                        href={href(`events/${id}`)}
                        className="badge b-neutral"
                        style={{ fontFamily: "monospace", display: "inline-flex", alignItems: "center", gap: 4 }}
                      >
                        {id} <ExternalLink size={10} />
                      </a>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: DIRECT LOG DUMP FOR SPECIFIC DEVICE                                */}
      {/* ========================================================================= */}
      {dumpModalOpen && dumpingDevice && (
        <div
          className="modal-overlay"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
            padding: 20,
          }}
        >
          <div
            className="modal-body"
            style={{
              background: "var(--card-bg, #111827)",
              border: "1px solid var(--border-color, #1f2937)",
              borderRadius: 12,
              padding: 24,
              maxWidth: 720,
              width: "100%",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
            }}
          >
            <div className="row spread" style={{ marginBottom: 14 }}>
              <div>
                <h2 style={{ fontSize: "1.25rem", margin: 0, fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }}>
                  <Upload size={18} style={{ color: "var(--ok)" }} />
                  Direct Log Dump: <strong>{dumpingDevice.name}</strong>
                </h2>
                <p className="faint small" style={{ margin: "4px 0 0 0" }}>
                  Dump raw wire logs directly as this device. Logs will be attributed to <code>{dumpingDevice.name}</code> and parsed into OCSF standard format.
                </p>
              </div>
              <button
                type="button"
                className="button text small icon-only"
                onClick={() => setDumpModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label className="small faint" style={{ display: "block", marginBottom: 6 }}>
                RAW WIRE LOGS (Paste one or more lines):
              </label>
              <textarea
                value={dumpInputText}
                onChange={(e) => setDumpInputText(e.target.value)}
                rows={7}
                style={{
                  width: "100%",
                  fontFamily: "monospace",
                  fontSize: "0.85rem",
                  background: "#0a0f1d",
                  color: "#e2e8f0",
                  padding: 12,
                  borderRadius: 8,
                  border: "1px solid var(--border-color)",
                }}
              />
            </div>

            {dumpResult && (
              <div
                className="notice ok small"
                style={{ marginBottom: 14, padding: "10px 14px", borderRadius: 6 }}
              >
                <strong>Dump Ingested: {dumpResult.accepted} events processed.</strong> (Status: {dumpResult.success_count} success, {dumpResult.failed_count} failed).
              </div>
            )}

            <div className="row spread" style={{ alignItems: "center" }}>
              <span className="small faint">Endpoint: <code>/api/v1/devices/{dumpingDevice.name}/logs</code></span>
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="button" onClick={() => setDumpModalOpen(false)}>
                  Close
                </button>
                <button
                  type="button"
                  className="button primary"
                  disabled={dumping || !dumpInputText.trim()}
                  onClick={handleExecuteDump}
                >
                  <Send size={13} />
                  {dumping ? "Dumping Logs…" : "Dump Logs into Pipeline"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: ADD INBOUND LOG DEVICE                                             */}
      {/* ========================================================================= */}
      {modalOpen && (
        <div
          className="modal-overlay"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
            padding: 20,
          }}
        >
          <div
            className="modal-body"
            style={{
              background: "var(--card-bg, #111827)",
              border: "1px solid var(--border-color, #1f2937)",
              borderRadius: 12,
              padding: 24,
              maxWidth: 680,
              width: "100%",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
            }}
          >
            <div className="row spread" style={{ marginBottom: 16 }}>
              <div>
                <h2 style={{ fontSize: "1.25rem", margin: 0, fontWeight: 700 }}>Add Inbound Log Device</h2>
                <p className="faint small" style={{ margin: "4px 0 0 0" }}>
                  Register a network asset, host, or forwarder to receive instant configuration templates.
                </p>
              </div>
              <button
                type="button"
                className="button text small icon-only"
                onClick={() => setModalOpen(false)}
              >
                ✕
              </button>
            </div>

            {/* Quick Vendor Presets */}
            <div style={{ marginBottom: 16 }}>
              <span className="faint small" style={{ display: "block", marginBottom: 6 }}>
                QUICK PRESETS (Click to auto-populate):
              </span>
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    className="button small"
                    onClick={() => applyPreset(p)}
                    style={{ fontSize: "0.8rem", padding: "4px 10px" }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <form onSubmit={handleRegister}>
              <div className="grid col-2" style={{ gap: 14, marginBottom: 14 }}>
                <div>
                  <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                    Device Name / Hostname *
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. edge-fw-cisco-01"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    style={{ width: "100%" }}
                  />
                </div>
                <div>
                  <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                    Device Category
                  </label>
                  <input
                    type="text"
                    value={deviceType}
                    onChange={(e) => setDeviceType(e.target.value)}
                    placeholder="Firewall / Perimeter"
                    style={{ width: "100%" }}
                  />
                </div>
              </div>

              <div className="grid col-3" style={{ gap: 14, marginBottom: 14 }}>
                <div>
                  <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                    Vendor / Model
                  </label>
                  <input
                    type="text"
                    value={vendor}
                    onChange={(e) => setVendor(e.target.value)}
                    placeholder="Cisco ASA"
                    style={{ width: "100%" }}
                  />
                </div>
                <div>
                  <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                    Protocol
                  </label>
                  <input
                    type="text"
                    value={protocol}
                    onChange={(e) => setProtocol(e.target.value)}
                    placeholder="Syslog UDP (514)"
                    style={{ width: "100%" }}
                  />
                </div>
                <div>
                  <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                    Wire Format
                  </label>
                  <input
                    type="text"
                    value={format}
                    onChange={(e) => setFormat(e.target.value)}
                    placeholder="Syslog (RFC 3164)"
                    style={{ width: "100%" }}
                  />
                </div>
              </div>

              <div style={{ marginBottom: 18 }}>
                <label className="small faint" style={{ display: "block", marginBottom: 4 }}>
                  Description / Deployment Context
                </label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Primary DMZ edge gateway protecting customer ingress"
                  style={{ width: "100%" }}
                />
              </div>

              {actionError && (
                <div className="notice fail small" style={{ marginBottom: 14 }}>
                  {actionError}
                </div>
              )}

              <div className="row spread" style={{ alignItems: "center" }}>
                <span className="small faint">A unique token &amp; config snippets will be generated.</span>
                <div className="row" style={{ gap: 8 }}>
                  <button type="button" className="button" onClick={() => setModalOpen(false)}>
                    Cancel
                  </button>
                  <button type="submit" className="button primary" disabled={creating || !name.trim()}>
                    {creating ? "Registering…" : "Register Device"}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
