#!/usr/bin/env python3
"""Build a tiny YOLO-like ONNX (1x84xN) for CI load tests — not for accuracy."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    try:
        import numpy as np
        from onnx import TensorProto, helper, numpy_helper, save, checker
    except ImportError:
        print("pip install onnx  # required to generate tiny model")
        return 1

    out = Path(__file__).resolve().parents[1] / "models" / "tiny_yolo_like_320.onnx"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Input must be consumed; scale to zero then add constant detections.
    # images [1,3,320,320] -> GlobalAveragePool -> [1,3,1,1] -> Flatten [1,3]
    # -> MatMul with W[3, 84*8] -> Reshape [1,84,8] + det_const

    n_anchors = 8
    X = helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 320, 320])
    Y = helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84, n_anchors])

    gap = helper.make_node("GlobalAveragePool", ["images"], ["gap"])
    flat = helper.make_node("Flatten", ["gap"], ["flat"], axis=1)

    w = np.zeros((3, 84 * n_anchors), dtype=np.float32)
    W = numpy_helper.from_array(w, name="W")
    matmul = helper.make_node("MatMul", ["flat", "W"], ["proj"])

    shape = numpy_helper.from_array(
        np.array([1, 84, n_anchors], dtype=np.int64), name="shape_out"
    )
    reshape = helper.make_node("Reshape", ["proj", "shape_out"], ["proj_r"])

    # Synthetic detection at anchor 0: person near center of 320 canvas
    const = np.zeros((1, 84, n_anchors), dtype=np.float32)
    const[0, 0, 0] = 160.0  # cx
    const[0, 1, 0] = 160.0  # cy
    const[0, 2, 0] = 50.0  # w
    const[0, 3, 0] = 100.0  # h
    const[0, 4, 0] = 0.99  # person score
    det = numpy_helper.from_array(const, name="det_const")
    add = helper.make_node("Add", ["proj_r", "det_const"], ["output0"])

    graph = helper.make_graph(
        [gap, flat, matmul, reshape, add],
        "tiny_yolo_like",
        [X],
        [Y],
        [W, shape, det],
    )
    model = helper.make_model(
        graph,
        opset_imports=[helper.make_opsetid("", 13)],
        producer_name="rasaops-make_tiny_onnx",
    )
    model.ir_version = 8
    checker.check_model(model)
    save(model, str(out))
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
