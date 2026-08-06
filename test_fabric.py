import shutil
import subprocess
import tempfile
import unittest
from math import hypot
from pathlib import Path
from xml.etree import ElementTree

import fabric


SOURCE = '''fabric fabric_demo #(
    parameter integer PAYLOAD_W = 8
) begin

    direct_src.tx.direct > direct_dst.rx;

    source_a.tx.to_sink -> sink.rx.a;
    source_d.tx.to_sink --> sink.rx.d;

endfabric
'''


class FabricTests(unittest.TestCase):
    def assert_no_diagram_overlap(self, diagram: str) -> None:
        root = ElementTree.fromstring(diagram)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        nodes = [
            (
                float(node.attrib["cx"]),
                float(node.attrib["cy"]),
                float(node.attrib["r"]) + 14,
            )
            for node in root.findall(".//svg:circle[@data-kind='unit']", namespace)
        ] + [
            (float(node.attrib["cx"]), float(node.attrib["cy"]), 52)
            for node in root.findall(".//svg:circle[@data-kind='router']", namespace)
        ]
        for index, (x1, y1, radius1) in enumerate(nodes):
            for x2, y2, radius2 in nodes[index + 1:]:
                self.assertGreaterEqual(hypot(x2 - x1, y2 - y1) + 0.01, radius1 + radius2)

    def test_parser_keeps_arrow_tiers_and_source_bindings(self):
        design = fabric.parse_fabric(SOURCE)
        self.assertEqual(design.name, "fabric_demo")
        self.assertEqual([connection.tier for connection in design.connections], [0, 1, 2])
        self.assertTrue(design.connections[0].direct)
        self.assertEqual(design.connections[1].recognized_source, "a")
        self.assertEqual(design.connections[2].destination.text, "sink.rx")

    def test_routes_are_deterministic_reversible_and_unique_per_input(self):
        design = fabric.parse_fabric(SOURCE)
        first = fabric.build_topology(design)
        second = fabric.build_topology(design)
        self.assertEqual(first.path_width, 2)
        self.assertEqual(first.routers, second.routers)
        self.assertEqual(first.routes, second.routes)
        delivered = set()
        for connection, route in first.routes.items():
            self.assertEqual(fabric.forward_trace(first, connection.source, route.path_word), fabric.endpoint_for_destination(connection.destination))
            self.assertEqual(fabric.reverse_trace(first, connection.destination, route.delivered_word), fabric.endpoint_for_source(connection.source))
            self.assertEqual(fabric.forward_trace_from_endpoint(first, fabric.endpoint_for_destination(connection.destination), fabric.return_path(route.delivered_word, first.path_width)), fabric.endpoint_for_source(connection.source))
            self.assertNotIn((connection.destination, route.delivered_word), delivered)
            delivered.add((connection.destination, route.delivered_word))

    def test_topology_prunes_empty_padding_subtrees(self):
        root = Path(__file__).parent
        design = fabric.parse_fabric((root / "examples" / "mixer_network.fabric").read_text())
        topology = fabric.build_topology(design)
        for router, ports in topology.routers.items():
            live_ports = sum(attachment is not None and attachment.kind != "dummy" for attachment in ports)
            self.assertGreaterEqual(live_ports, 2, router)

    def test_direct_destination_is_exclusive(self):
        invalid = SOURCE.replace("    source_a.tx.to_sink -> sink.rx.a;", "    source_a.tx.to_sink > direct_dst.rx;")
        with self.assertRaisesRegex(fabric.PigenError, "direct destination"):
            fabric.parse_fabric(invalid)

    def test_v0_buffer_depth_is_explicitly_fixed_at_two(self):
        invalid = SOURCE.replace("\n\n    direct_src", "\n    option router_buffer_depth = 1;\n\n    direct_src")
        with self.assertRaisesRegex(fabric.PigenError, "fixes .* at 2"):
            fabric.parse_fabric(invalid)

    def test_direct_only_fabric_has_no_route_width_or_router(self):
        design = fabric.parse_fabric('''fabric direct_only #(
    parameter integer PAYLOAD_W = 8
) begin
    left.tx.right > right.rx;
endfabric
''')
        rtl, manifest, topology = fabric.generate_fabric(design)
        self.assertEqual(topology.path_width, 0)
        self.assertNotIn("direct_only__router", rtl)
        self.assertNotIn("PATH_W", rtl)
        self.assertIn("direct", manifest)

    def test_diagram_is_deterministic_and_represents_every_generated_part(self):
        design = fabric.parse_fabric(SOURCE)
        topology = fabric.build_topology(design)
        diagram = fabric.render_diagram(design, topology)
        self.assertEqual(diagram, fabric.render_diagram(design, topology))
        self.assertTrue(diagram.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        root = ElementTree.fromstring(diagram)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        self.assertEqual(root.attrib["data-layout"], "tree-spring")
        self.assertGreaterEqual(int(root.attrib["data-crossings"]), 0)
        self.assertLessEqual(int(root.attrib["data-crossings"]), int(root.attrib["data-crossings-before"]))
        units = root.findall(".//svg:circle[@data-kind='unit']", namespace)
        self.assertTrue(units)
        expected_units = {connection.source.instance for connection in design.connections} | {connection.destination.instance for connection in design.connections}
        self.assertEqual({unit.attrib["data-unit"] for unit in units}, expected_units)
        self.assert_no_diagram_overlap(diagram)
        for router in topology.routers:
            self.assertIn(f'data-router="{router}"', diagram)
        for endpoint in topology.endpoint_ports:
            self.assertIn(f'data-endpoint="{endpoint.text}"', diagram)
        for connection in design.connections:
            self.assertIn(f'data-endpoint="source:{connection.source.text}"', diagram)
            self.assertIn(f'data-endpoint="destination:{connection.destination.text}"', diagram)
        self.assertIn('data-kind="direct-link"', diagram)
        self.assertIn('data-kind="router-link"', diagram)

    def test_mixer_network_diagram_has_no_overlapping_nodes(self):
        root = Path(__file__).parent
        design = fabric.parse_fabric((root / "examples" / "mixer_network.fabric").read_text())
        diagram = fabric.render_diagram(design, fabric.build_topology(design))
        self.assert_no_diagram_overlap(diagram)
        svg = ElementTree.fromstring(diagram)
        self.assertEqual(svg.attrib["data-direct-crossings"], "0")
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        direct_links = svg.findall(".//svg:line[@data-kind='direct-link']", namespace)
        self.assertEqual(len(direct_links), 2)
        self.assertTrue(all("marker-end" not in link.attrib for link in direct_links))
        wire_lengths = {
            kind: [hypot(float(link.attrib["x2"]) - float(link.attrib["x1"]), float(link.attrib["y2"]) - float(link.attrib["y1"])) for link in svg.findall(f".//svg:line[@data-kind='{kind}']", namespace)]
            for kind in ("router-link", "endpoint-link", "direct-link")
        }
        routed_average = sum(wire_lengths["router-link"] + wire_lengths["endpoint-link"]) / (len(wire_lengths["router-link"]) + len(wire_lengths["endpoint-link"]))
        direct_average = sum(wire_lengths["direct-link"]) / len(wire_lengths["direct-link"])
        self.assertLess(abs(direct_average - routed_average), routed_average * 0.15)
        port_labels = []
        for label in svg.findall(".//svg:text[@class='port'][@data-endpoint]", namespace):
            text = "".join(label.itertext())
            width = len(text) * 6.1
            x, y, anchor = float(label.attrib["x"]), float(label.attrib["y"]), label.attrib.get("text-anchor", "start")
            left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
            port_labels.append((left, y - 11, left + width, y + 3))
        for index, first in enumerate(port_labels):
            for second in port_labels[index + 1:]:
                self.assertFalse(first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3])

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus Verilog is not installed")
    def test_direct_only_rtl_compiles(self):
        design = fabric.parse_fabric('''fabric direct_only #(
    parameter integer PAYLOAD_W = 8
) begin
    left.tx.right > right.rx;
endfabric
''')
        rtl, _, _ = fabric.generate_fabric(design)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "direct_only.sv"
            source.write_text(rtl)
            result = subprocess.run(["iverilog", "-g2012", "-s", "direct_only", "-o", str(Path(directory) / "sim"), str(source)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_writes_rtl_manifest_and_default_diagram(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, output = directory / "demo.fabric", directory / "demo.sv"
            source.write_text(SOURCE)
            result = subprocess.run(["python3", "pigen.py", "fabric", str(source), "-o", str(output)], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("module fabric_demo", output.read_text())
            self.assertIn("source_a.tx.to_sink", output.with_suffix(".sv.routes").read_text())
            diagram = output.with_suffix(".sv.svg").read_text()
            self.assertIn('<svg ', diagram)
            self.assertIn('data-kind="direct-link"', diagram)

    def test_cli_allows_custom_or_suppressed_diagram(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, output, diagram = directory / "demo.fabric", directory / "demo.sv", directory / "network.svg"
            source.write_text(SOURCE)
            result = subprocess.run(["python3", "pigen.py", "fabric", str(source), "-o", str(output), "--diagram", str(diagram)], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(diagram.exists())
            self.assertFalse(output.with_suffix(".sv.svg").exists())
            result = subprocess.run(["python3", "pigen.py", "fabric", str(source), "-o", str(output), "--no-diagram"], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_example_generates_compiles_and_simulates(self):
        root = Path(__file__).parent
        design = fabric.parse_fabric((root / "examples" / "fabric_demo.fabric").read_text())
        rtl, _, _ = fabric.generate_fabric(design)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, executable = directory / "fabric_demo.sv", directory / "sim"
            source.write_text(rtl)
            result = subprocess.run(["iverilog", "-g2012", "-s", "fabric_demo_tb", "-o", str(executable), str(source), str(root / "examples" / "fabric_demo_tb.sv")], cwd=root / "examples", text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["vvp", str(executable)], cwd=root / "examples", text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_router_arbitrates_and_rotates_paths(self):
        testbench = '''
module router_tb;
  logic clk = 0, reset = 1, enable = 1;
  logic p0_in_valid = 0, p0_in_ready;
  logic [5:0] p0_in_packet = 0;
  logic p0_out_valid, p0_out_ready = 1;
  logic [5:0] p0_out_packet;
  logic p1_in_valid = 0, p1_in_ready;
  logic [5:0] p1_in_packet = 0;
  logic p1_out_valid, p1_out_ready = 1;
  logic [5:0] p1_out_packet;
  logic p2_in_valid = 0, p2_in_ready;
  logic [5:0] p2_in_packet = 0;
  logic p2_out_valid, p2_out_ready = 1;
  logic [5:0] p2_out_packet;
  router_unit__router #(.PAYLOAD_W(4), .PATH_W(2)) dut (.*);
  always #5 clk = ~clk;
  initial begin
    repeat (2) @(posedge clk);
    reset = 0;
    @(negedge clk);
    // p0 -> p1 uses clockwise bit 0; p2 -> p1 uses counter-clockwise bit 1.
    p0_in_packet = {2'b00, 4'ha}; p0_in_valid = 1;
    p2_in_packet = {2'b01, 4'hb}; p2_in_valid = 1;
    @(posedge clk);
    @(negedge clk);
    p0_in_valid = 0; p2_in_valid = 0;
    if (!p1_out_valid || p1_out_packet !== {2'b00, 4'ha})
      $fatal(1, "first arbitration/rotation result incorrect");
    @(posedge clk);
    #1;
    if (!p1_out_valid || p1_out_packet !== {2'b10, 4'hb})
      $fatal(1, "round-robin or second rotation result incorrect");
    @(posedge clk);
    $finish;
  end
endmodule
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, executable = root / "router_tb.sv", root / "sim"
            source.write_text(fabric.render_router("router_unit") + testbench)
            result = subprocess.run(["iverilog", "-g2012", "-s", "router_tb", "-o", str(executable), str(source)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_skid_accepts_a_replacement_every_cycle(self):
        testbench = '''
module skid_tb;
  logic clk = 0, reset = 1, enable = 1;
  logic in_valid = 0, in_ready;
  logic [7:0] packet_in = 0;
  logic out_valid, out_ready = 1;
  logic [7:0] packet_out;
  integer received = 0;
  skid_unit__skid #(.PACKET_W(8)) dut (.*);
  always #5 clk = ~clk;
  always @(posedge clk) begin
    if (!reset && out_valid && out_ready) begin
      if (packet_out !== received[7:0])
        $fatal(1, "expected packet %0d, got %0d", received, packet_out);
      received <= received + 1;
    end
  end
  initial begin
    repeat (2) @(posedge clk);
    reset = 0;
    for (integer value = 0; value < 8; value = value + 1) begin
      @(negedge clk);
      packet_in = value;
      in_valid = 1;
      @(posedge clk);
      if (!in_ready) $fatal(1, "skid stalled before packet %0d", value);
    end
    @(negedge clk);
    in_valid = 0;
    wait (received == 8);
    $finish;
  end
endmodule
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, executable = root / "skid_tb.sv", root / "sim"
            source.write_text(fabric.render_skid("skid_unit") + testbench)
            result = subprocess.run(["iverilog", "-g2012", "-s", "skid_tb", "-o", str(executable), str(source)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_randomized_ready_valid_traffic(self):
        source_text = '''fabric random_fabric #(
    parameter integer PAYLOAD_W = 8
) begin
    a.tx.sink -> sink.rx.a;
    d.tx.sink -> sink.rx.d;
endfabric
'''
        testbench = '''
module random_fabric_tb;
  logic clk = 0, reset = 1, enable = 1;
  logic a__tx__sink__valid = 0, a__tx__sink__ready;
  logic [7:0] a__tx__sink__payload = 0;
  logic d__tx__sink__valid = 0, d__tx__sink__ready;
  logic [7:0] d__tx__sink__payload = 0;
  logic sink__rx__valid, sink__rx__ready = 0;
  logic [7:0] sink__rx__payload;
  logic [2:0] sink__rx__path;
  integer sent_a = 0, sent_d = 0, received = 0;
  logic [31:0] seen = 0;
  random_fabric dut (.*);
  always #5 clk = ~clk;
  always @(negedge clk) begin
    if (!reset) begin
      sink__rx__ready = $urandom_range(0, 1);
      if (!a__tx__sink__valid && sent_a < 16 && $urandom_range(0, 1)) begin
        a__tx__sink__payload = sent_a;
        a__tx__sink__valid = 1;
      end
      if (!d__tx__sink__valid && sent_d < 16 && $urandom_range(0, 1)) begin
        d__tx__sink__payload = 8'd128 + sent_d;
        d__tx__sink__valid = 1;
      end
    end
  end
  always @(posedge clk) begin
    integer index;
    if (!reset) begin
      if (a__tx__sink__valid && a__tx__sink__ready) begin
        sent_a <= sent_a + 1;
        a__tx__sink__valid <= 0;
      end
      if (d__tx__sink__valid && d__tx__sink__ready) begin
        sent_d <= sent_d + 1;
        d__tx__sink__valid <= 0;
      end
      if (sink__rx__valid && sink__rx__ready) begin
        if (sink__rx__payload < 16)
          index = sink__rx__payload;
        else if (sink__rx__payload >= 128 && sink__rx__payload < 144)
          index = 16 + sink__rx__payload - 128;
        else
          $fatal(1, "unexpected payload %0d", sink__rx__payload);
        if (seen[index]) $fatal(1, "duplicate payload %0d", sink__rx__payload);
        seen[index] <= 1'b1;
        received <= received + 1;
      end
    end
  end
  initial begin
    repeat (2) @(posedge clk);
    reset = 0;
    repeat (1000) begin
      @(posedge clk);
      if (received == 32) begin
        if (seen !== 32'hffff_ffff) $fatal(1, "missing payloads");
        $finish;
      end
    end
    $fatal(1, "randomized traffic timed out: received %0d", received);
  end
endmodule
'''
        design = fabric.parse_fabric(source_text)
        rtl, _, _ = fabric.generate_fabric(design)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, executable = root / "random.sv", root / "sim"
            source.write_text(rtl + testbench)
            result = subprocess.run(["iverilog", "-g2012", "-s", "random_fabric_tb", "-o", str(executable), str(source)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
