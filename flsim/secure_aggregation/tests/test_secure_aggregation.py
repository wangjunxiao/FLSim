#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import torch
from flsim.channels.message import Message
from flsim.common.pytest_helper import assertEqual, assertRaises, assertTrue
from flsim.secure_aggregation.secure_aggregator import (
    FixedPointConfig,
    FixedPointConverter,
    SecureAggregator,
    utility_config_flatter,
)
from flsim.servers.sync_secagg_servers import SyncSecAggServerConfig
from flsim.utils import test_utils as utils
from flsim.utils.fl.common import FLModelParamUtils
from flsim.utils.test_utils import (
    create_model_with_value,
    model_parameters_equal_to_value,
    SampleNet,
)
from hydra.utils import instantiate
from omegaconf import OmegaConf


class TestSecureAggregator:
    def _create_model(self, model_param_value):
        """
        Creates a two-layer model
        """
        fl_model = utils.SampleNet(utils.TwoFC())
        fl_model.fl_get_module().fill_all(model_param_value)
        return fl_model.fl_get_module()

    def _create_server(self, model, fixedpoint, channel=None):
        return instantiate(
            SyncSecAggServerConfig(fixedpoint=fixedpoint),
            global_model=model,
            channel=channel,
        )

    def test_fixedpoint_init(self) -> None:
        """
        Tests that FixedPointConverter init works correctly
        """

        converter = FixedPointConverter(
            **OmegaConf.structured(FixedPointConfig(num_bytes=2, scaling_factor=1000))
        )
        assertEqual(converter.max_value, 32767)
        assertEqual(converter.min_value, -32768)

        with assertRaises(ValueError):
            converter = FixedPointConverter(
                **OmegaConf.structured(
                    FixedPointConfig(num_bytes=9, scaling_factor=1000)
                )
            )

        with assertRaises(ValueError):
            converter = FixedPointConverter(
                **OmegaConf.structured(
                    FixedPointConfig(num_bytes=3, scaling_factor=-100)
                )
            )

    def test_floating_to_fixedpoint(self) -> None:
        """
        Tests whether conversion from floating point to fixed point works
        """

        #  hence minValue = -32768, maxValue = 32767
        converter = FixedPointConverter(
            **OmegaConf.structured(FixedPointConfig(num_bytes=2, scaling_factor=100))
        )

        x = torch.tensor(17.42)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = 1742.0 ==> round to 1742
        assertEqual(y, torch.tensor(1742))

        x = torch.tensor(17.4298)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = 1742.98 ==> round to 1743
        assertEqual(y, torch.tensor(1743))

        x = torch.tensor(-2.34)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = -234.0 ==> round to -234
        assertEqual(y, torch.tensor(-234))

        x = torch.tensor(-2.3456)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = -234.56 ==> round to -235
        assertEqual(y, torch.tensor(-235))

        x = torch.tensor(-2.3416)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = -234.16 ==> round to -234
        assertEqual(y, torch.tensor(-234))

        x = torch.tensor(12345.0167)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = 1234501.67 ==> adjust to maxValue 32767
        assertEqual(y, torch.tensor(32767))

        x = torch.tensor(-327.69)
        y = converter.to_fixedpoint(x)
        # y = x * scaling_factor = -32769 ==> adjust to minValue -32768
        assertEqual(y, torch.tensor(-32768))

    def test_fixed_to_floating_point(self) -> None:
        """
        Tests whether conversion from fixed point to floating point works
        """

        converter = FixedPointConverter(
            **OmegaConf.structured(FixedPointConfig(num_bytes=1, scaling_factor=85))
        )

        x = torch.tensor(85)
        y = converter.to_float(x)
        # y = x / scaling_factor = 1.0
        assertTrue(torch.allclose(y, torch.tensor(1.0), rtol=1e-10))

        x = torch.tensor(157)
        y = converter.to_float(x)
        # y = x / scaling_factor = 1.847058823529412
        assertTrue(torch.allclose(y, torch.tensor(1.847058823529412), rtol=1e-10))

    def test_params_floating_to_fixedpoint(self) -> None:
        """
        Tests whether the parameters of a model are converted correctly
        from floating point to fixed point
        """

        #  hence minValue = -32768, maxValue = 32767
        config = FixedPointConfig(num_bytes=2, scaling_factor=100)

        model = self._create_model(6.328)
        secure_aggregator = SecureAggregator(utility_config_flatter(model, config))
        secure_aggregator.params_to_fixedpoint(model)
        mismatched = utils.model_parameters_equal_to_value(model, 633.0)
        assertEqual(mismatched, "", mismatched)

        model = self._create_model(-3.8345)
        secure_aggregator = SecureAggregator(utility_config_flatter(model, config))
        secure_aggregator.params_to_fixedpoint(model)
        mismatched = utils.model_parameters_equal_to_value(model, -383.0)
        assertEqual(mismatched, "", mismatched)

    def test_params_floating_to_fixedpoint_different_config_for_layers(self) -> None:
        """
        Tests whether the parameters of a model are converted correctly
        from floating point to fixed point, when we have different
        FixedPointConverter configs for different layers

        """

        config_layer1 = FixedPointConfig(num_bytes=2, scaling_factor=100)
        #  hence minValue = -32768, maxValue = 32767
        config_layer2 = FixedPointConfig(num_bytes=1, scaling_factor=10)
        #  hence minValue = -128, maxValue = 127

        config = {}
        config["fc1.weight"] = config_layer1
        config["fc1.bias"] = config_layer1
        config["fc2.weight"] = config_layer2
        config["fc2.bias"] = config_layer2

        model = self._create_model(5.4728)
        secure_aggregator = SecureAggregator(config)
        secure_aggregator.params_to_fixedpoint(model)
        for name, p in model.named_parameters():
            if name == "fc1.weight" or name == "fc1.bias":
                # round 547.28 to 547
                assertTrue(torch.allclose(p, torch.tensor(547.0), rtol=1e-10))
            if name == "fc2.weight" or name == "fc2.bias":
                # round 54.728 to 55
                assertTrue(torch.allclose(p, torch.tensor(55.0), rtol=1e-10))

    def test_error_raised_per_layer_config_not_set(self) -> None:
        """
        Tests whether all layers have their corresponding configs, when
        per layer fixed point converter is used.
        """

        config_layer1 = FixedPointConfig(num_bytes=8, scaling_factor=10000)

        config = {}
        config["fc1.weight"] = config_layer1
        config["fc1.bias"] = config_layer1

        model = self._create_model(600)
        secure_aggregator = SecureAggregator(config)

        with assertRaises(ValueError):
            secure_aggregator.params_to_float(model)

        with assertRaises(ValueError):
            secure_aggregator.params_to_fixedpoint(model)

    def test_params_fixed_to_floating_point(self) -> None:
        """
        Tests whether the parameters of a model are converted correctly
        from fixed point to floating point
        """
        config = FixedPointConfig(num_bytes=3, scaling_factor=40)
        model = self._create_model(880.0)
        secure_aggregator = SecureAggregator(utility_config_flatter(model, config))
        secure_aggregator.params_to_float(model)
        mismatched = utils.model_parameters_equal_to_value(model, 22.0)
        assertEqual(mismatched, "", mismatched)

    def test_params_fixed_to_floating_point_different_config_for_layers(self) -> None:
        """
        Tests whether the parameters of a model are converted correctly
        from fixed point to floating point, when we have different
        FixedPointConverter configs for different layers
        """
        config_layer1 = FixedPointConfig(num_bytes=2, scaling_factor=30)
        config_layer2 = FixedPointConfig(num_bytes=1, scaling_factor=80)

        config = {}
        config["fc1.weight"] = config_layer1
        config["fc1.bias"] = config_layer1
        config["fc2.weight"] = config_layer2
        config["fc2.bias"] = config_layer2

        model = self._create_model(832.8)
        secure_aggregator = SecureAggregator(config)
        secure_aggregator.params_to_float(model)
        for name, p in model.named_parameters():
            if name == "fc1.weight" or name == "fc1.bias":
                # 832.8 / 30 = 27.76
                assertTrue(torch.allclose(p, torch.tensor(27.76), rtol=1e-10))
            if name == "fc2.weight" or name == "fc2.bias":
                # 832.8 / 80 = 10.41
                assertTrue(torch.allclose(p, torch.tensor(10.41), rtol=1e-10))

    def test_conversion_overflow(self) -> None:
        """
        Tests whether secure aggeragtion conversion overflow
        variable gets updated correctly
        """
        model = self._create_model(70.0)
        # freeze one of the two linear layers
        for p in model.fc2.parameters():
            p.requires_grad = False
        config = FixedPointConfig(num_bytes=1, scaling_factor=10)
        # hence minValue = -128, maxValue = 127
        secure_aggregator = SecureAggregator(utility_config_flatter(model, config))

        for name, _ in FLModelParamUtils.get_trainable_named_parameters(model):
            assertEqual(secure_aggregator.converters[name].get_convert_overflow(), 0)

        secure_aggregator.params_to_fixedpoint(model)
        # 70 * 10 = 700. Overflow occurs for all parameters
        # model : --[fc1=(2,5)]--[fc2=(5,1)]--
        assertEqual(
            secure_aggregator.converters["fc1.weight"].get_convert_overflow(), 10
        )
        assertEqual(secure_aggregator.converters["fc1.bias"].get_convert_overflow(), 5)
        assertTrue("fc2.weight" not in secure_aggregator.converters.keys())
        assertTrue("fc2.bias" not in secure_aggregator.converters.keys())

        # test reset conversion overflow
        for name, _ in FLModelParamUtils.get_trainable_named_parameters(model):
            secure_aggregator.converters[name].get_convert_overflow(reset=True)
            assertEqual(secure_aggregator.converters[name].get_convert_overflow(), 0)

    def test_secure_aggregator_step_large_range(self) -> None:
        """
        Tests whether secure aggregation operations work correctly
        when the step() method is called, and when the num_bytes is
        big, so we do not have a possible fixedpoint overflow
        """
        scaling_factor = 10
        num_bytes = 4
        global_param = 8.0
        client_param = 2.123
        num_clients = 10

        fixedpoint = FixedPointConfig(
            num_bytes=num_bytes, scaling_factor=scaling_factor
        )
        server = self._create_server(
            SampleNet(create_model_with_value(global_param)), fixedpoint=fixedpoint
        )

        clients = [create_model_with_value(client_param) for _ in range(num_clients)]

        server.init_round()
        for client in clients:
            server.receive_update_from_client(Message(SampleNet(client), weight=1.0))

        expected_param = float(round(global_param - client_param, ndigits=1))

        server.step()
        mismatched = model_parameters_equal_to_value(
            server.global_model.fl_get_module(), expected_param
        )
        assertEqual(mismatched, "", mismatched)

    def test_secure_aggregator_step_small_range(self) -> None:
        """
        Tests whether secure aggregation operations work correctly
        when the step() method is called, and when the num_bytes is
        small so we have possible fixedpoint conversion overflows
        """
        scaling_factor = 100
        num_bytes = 1
        global_param = 8
        client_param = 2.123
        num_clients = 10

        fixedpoint = FixedPointConfig(
            num_bytes=num_bytes, scaling_factor=scaling_factor
        )
        server = self._create_server(
            SampleNet(create_model_with_value(global_param)), fixedpoint=fixedpoint
        )

        clients = [create_model_with_value(client_param) for _ in range(num_clients)]

        server.init_round()
        for client in clients:
            server.receive_update_from_client(Message(SampleNet(client), weight=1.0))

        # when a client update is converted to fixedpoint: 2.123 -> 212.3 -> 127.
        # when adding `num_clients` updates, the sum would actually get smaller, i.e.
        # 127+127+..+127=128-num_clients in bit representation when `num_bytes=1.
        # So, the update is (128-10)/10 = 11.8 (in fixedpoint). Convert to float is 0.118
        expected_param = float(global_param - (0.118 * num_clients) / num_clients)

        server.step()
        mismatched = model_parameters_equal_to_value(
            server.global_model.fl_get_module(), expected_param
        )
        assertEqual(mismatched, "", mismatched)

        client_param = 0.2
        clients = [create_model_with_value(client_param) for _ in range(num_clients)]

        server.init_round()
        for client in clients:
            server.receive_update_from_client(Message(SampleNet(client), weight=1.0))

        # when a client update is converted to fixedpoint: 0.2 -> 20.
        # when adding `num_clients` updates, the sum would actually get smaller, i.e.
        # 20+20+..+20=(200%128)=72 in bit representation when `num_bytes=1.
        # So, the update is (72)/10 = 7.2 (in fixedpoint). Convert to float is 0.072
        new_expected_param = float(expected_param - (0.072 * num_clients) / num_clients)

        server.step()
        mismatched = model_parameters_equal_to_value(
            server.global_model.fl_get_module(), new_expected_param
        )
        assertEqual(mismatched, "", mismatched)

    def test_aggregation_overflow(self) -> None:
        """
        Tests whether secure aggregation overflow
        variable are updated correctly during aggregation
        """
        scaling_factor = 10
        num_bytes = 1
        global_param = 6
        client_param = 2.8
        num_clients = 10

        fixedpoint = FixedPointConfig(
            num_bytes=num_bytes, scaling_factor=scaling_factor
        )

        server_model = create_model_with_value(global_param)
        # freeze one of the two linear layers
        for p in server_model.fc2.parameters():  # pyre-ignore[16]
            p.requires_grad = False
        server = self._create_server(SampleNet(server_model), fixedpoint=fixedpoint)
        clients = [create_model_with_value(client_param) for _ in range(num_clients)]

        clients = []
        for _ in range(num_clients):
            client_model = create_model_with_value(client_param)
            for p in client_model.fc2.parameters():  # pyre-ignore[16]
                p.requires_grad = False
            clients.append(client_model)

        server.init_round()
        # model : --[fc1=(2,5)]--[fc2=(5,1)]--
        assertEqual(
            server._secure_aggregator.get_aggregate_overflow(),
            0,
        )

        for client in clients:
            server.receive_update_from_client(Message(SampleNet(client), weight=1.0))
        num_params = sum(
            p.numel()
            for p in server.global_model.fl_get_module().parameters()
            if p.requires_grad
        )

        # Client update in fixedpoint is 28. When adding `num_clients` updates,
        # the sum would overflow, i.e. 28+28+..+28=(280%128)=24 in bit representation
        # when `num_bytes=1, Hence [280/128]=2 aggr overflows occur for any parameter.
        assertEqual(
            server._secure_aggregator.get_aggregate_overflow(),
            2 * num_params,
        )

        # test reset aggregation overflow
        server._secure_aggregator.get_aggregate_overflow(reset=True)
        assertEqual(
            server._secure_aggregator.get_aggregate_overflow(),
            0,
        )
