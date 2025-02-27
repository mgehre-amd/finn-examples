# Copyright (c) 2020, Xilinx
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of FINN nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import finn.builder.build_dataflow as build
import finn.builder.build_dataflow_config as build_cfg
from finn.util.basic import alveo_default_platform
import os
import shutil

# custom steps for mobilenetv1
from custom_steps import (
    step_mobilenet_streamline,
    step_mobilenet_convert_to_hls_layers,
    step_mobilenet_convert_to_hls_layers_separate_th,
    step_mobilenet_lower_convs,
    step_mobilenet_slr_floorplan,
)

model_name = "mobilenetv1-w4a4"

# which platforms to build the networks for
zynq_platforms = ["ZCU102", "ZCU104"]
#alveo_platforms = ["U50", "U200", "U250", "U280"]
alveo_platforms = ["U250"]
platforms_to_build = zynq_platforms + alveo_platforms


# determine which shell flow to use for a given platform
def platform_to_shell(platform):
    if platform in zynq_platforms:
        return build_cfg.ShellFlowType.VIVADO_ZYNQ
    elif platform in alveo_platforms:
        return build_cfg.ShellFlowType.VITIS_ALVEO
    else:
        raise Exception("Unknown platform, can't determine ShellFlowType")


# select target clock frequency
def select_clk_period(platform):
    if platform in zynq_platforms:
        return 5.4
    elif platform in alveo_platforms:
        return 3.0


# select build steps (ZCU104/102 folding config is based on separate thresholding nodes)
def select_build_steps(platform):
    if platform in zynq_platforms:
        return [
            step_mobilenet_streamline,
            step_mobilenet_lower_convs,
            step_mobilenet_convert_to_hls_layers_separate_th,
            "step_create_dataflow_partition",
            "step_apply_folding_config",
            "step_generate_estimate_reports",
            "step_hls_codegen",
            "step_hls_ipgen",
            "step_set_fifo_depths",
            "step_create_stitched_ip",
            "step_synthesize_bitfile",
            "step_make_pynq_driver",
            "step_deployment_package",
        ]
    elif platform in alveo_platforms:
        return [
            step_mobilenet_streamline,
            step_mobilenet_lower_convs,
            step_mobilenet_convert_to_hls_layers,
            "step_create_dataflow_partition",
            "step_apply_folding_config",
            "step_generate_estimate_reports",
            "step_hls_codegen",
            "step_hls_ipgen",
            "step_set_fifo_depths",
            step_mobilenet_slr_floorplan,
            "step_synthesize_bitfile",
            "step_make_pynq_driver",
            "step_deployment_package",
        ]


# create a release dir, used for finn-examples release packaging
os.makedirs("release", exist_ok=True)


for platform_name in platforms_to_build:
    shell_flow_type = platform_to_shell(platform_name)
    if shell_flow_type == build_cfg.ShellFlowType.VITIS_ALVEO:
        vitis_platform = alveo_default_platform[platform_name]
        # for Alveo, use the Vitis platform name as the release name
        # e.g. xilinx_u250_xdma_201830_2
        release_platform_name = vitis_platform
    else:
        vitis_platform = None
        # for Zynq, use the board name as the release name
        # e.g. ZCU104
        release_platform_name = platform_name
    platform_dir = "release/%s" % release_platform_name
    os.makedirs(platform_dir, exist_ok=True)

    cfg = build_cfg.DataflowBuildConfig(
        steps=select_build_steps(platform_name),
        output_dir="output_%s_%s" % (model_name, release_platform_name),
        folding_config_file="folding_config/%s_folding_config.json" % platform_name,
        synth_clk_period_ns=select_clk_period(platform_name),
        board=platform_name,
        shell_flow_type=shell_flow_type,
        vitis_platform=vitis_platform,
        # folding config comes with FIFO depths already
        auto_fifo_depths=False,
        # enable extra performance optimizations (physopt)
        vitis_opt_strategy=build_cfg.VitisOptStrategyCfg.PERFORMANCE_BEST,
        generate_outputs=[
            build_cfg.DataflowOutputType.PYNQ_DRIVER,
            build_cfg.DataflowOutputType.ESTIMATE_REPORTS,
            build_cfg.DataflowOutputType.BITFILE,
            build_cfg.DataflowOutputType.DEPLOYMENT_PACKAGE,
        ],
    )
    model_file = "models/%s_pre_post_tidy.onnx" % model_name
    build.build_dataflow_cfg(model_file, cfg)

    # copy bitfiles and runtime weights into release dir if found
    bitfile_gen_dir = cfg.output_dir + "/bitfile"
    files_to_check_and_copy = [
        "finn-accel.bit",
        "finn-accel.hwh",
        "finn-accel.xclbin",
    ]
    for f in files_to_check_and_copy:
        src_file = bitfile_gen_dir + "/" + f
        dst_file = platform_dir + "/" + f.replace("finn-accel", model_name)
        if os.path.isfile(src_file):
            shutil.copy(src_file, dst_file)

    weight_gen_dir = cfg.output_dir + "/driver/runtime_weights"
    weight_dst_dir = platform_dir + "/%s_runtime_weights" % model_name
    if os.path.isdir(weight_gen_dir):
        weight_files = os.listdir(weight_gen_dir)
        if weight_files:
            shutil.copytree(weight_gen_dir, weight_dst_dir)

