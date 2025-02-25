# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# type: ignore
# pylint: skip-file

import logging
from os import environ
from typing import Dict, Iterable, Optional
from unittest import TestCase
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.environment_variables import OTEL_PYTHON_ID_GENERATOR
from opentelemetry.sdk._configuration import (
    _EXPORTER_OTLP,
    _EXPORTER_OTLP_PROTO_GRPC,
    _EXPORTER_OTLP_PROTO_HTTP,
    _get_exporter_names,
    _get_id_generator,
    _import_exporters,
    _import_id_generator,
    _init_logging,
    _init_metrics,
    _init_tracing,
    _initialize_components,
)
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.sdk._logs.export import ConsoleLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    ConsoleMetricExporter,
    Metric,
    MetricExporter,
    MetricReader,
)
from opentelemetry.sdk.metrics.view import Aggregation
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator


class Provider:
    def __init__(self, resource=None, id_generator=None):
        self.id_generator = id_generator
        self.processor = None
        self.resource = resource or Resource.create({})

    def add_span_processor(self, processor):
        self.processor = processor


class DummyLoggerProvider:
    def __init__(self, resource=None):
        self.resource = resource
        self.processor = DummyLogRecordProcessor(DummyOTLPLogExporter())

    def add_log_record_processor(self, processor):
        self.processor = processor

    def get_logger(self, name, *args, **kwargs):
        return DummyLogger(name, self.resource, self.processor)

    def force_flush(self, *args, **kwargs):
        pass


class DummyMeterProvider(MeterProvider):
    pass


class DummyLogger:
    def __init__(self, name, resource, processor):
        self.name = name
        self.resource = resource
        self.processor = processor

    def emit(self, record):
        self.processor.emit(record)


class DummyLogRecordProcessor:
    def __init__(self, exporter):
        self.exporter = exporter

    def emit(self, record):
        self.exporter.export([record])

    def force_flush(self, time):
        pass

    def shutdown(self):
        pass


class Processor:
    def __init__(self, exporter):
        self.exporter = exporter


class DummyMetricReader(MetricReader):
    def __init__(
        self,
        exporter: MetricExporter,
        preferred_temporality: Dict[type, AggregationTemporality] = None,
        preferred_aggregation: Dict[type, Aggregation] = None,
        export_interval_millis: Optional[float] = None,
        export_timeout_millis: Optional[float] = None,
    ) -> None:
        super().__init__(
            preferred_temporality=preferred_temporality,
            preferred_aggregation=preferred_aggregation,
        )
        self.exporter = exporter

    def _receive_metrics(
        self,
        metrics: Iterable[Metric],
        timeout_millis: float = 10_000,
        **kwargs,
    ) -> None:
        self.exporter.export(None)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        return True


class DummyOTLPMetricExporter:
    def __init__(self, *args, **kwargs):
        self.export_called = False

    def export(self, batch):
        self.export_called = True

    def shutdown(self):
        pass


class Exporter:
    def __init__(self):
        tracer_provider = trace.get_tracer_provider()
        self.service_name = (
            tracer_provider.resource.attributes[SERVICE_NAME]
            if getattr(tracer_provider, "resource", None)
            else Resource.create().attributes.get(SERVICE_NAME)
        )

    def shutdown(self):
        pass


class OTLPSpanExporter:
    pass


class DummyOTLPLogExporter:
    def __init__(self, *args, **kwargs):
        self.export_called = False

    def export(self, batch):
        self.export_called = True

    def shutdown(self):
        pass


class CustomIdGenerator(IdGenerator):
    def generate_span_id(self):
        pass

    def generate_trace_id(self):
        pass


class IterEntryPoint:
    def __init__(self, name, class_type):
        self.name = name
        self.class_type = class_type

    def load(self):
        return self.class_type


class TestTraceInit(TestCase):
    def setUp(self):
        super()
        self.get_provider_patcher = patch(
            "opentelemetry.sdk._configuration.TracerProvider", Provider
        )
        self.get_processor_patcher = patch(
            "opentelemetry.sdk._configuration.BatchSpanProcessor", Processor
        )
        self.set_provider_patcher = patch(
            "opentelemetry.sdk._configuration.set_tracer_provider"
        )

        self.get_provider_mock = self.get_provider_patcher.start()
        self.get_processor_mock = self.get_processor_patcher.start()
        self.set_provider_mock = self.set_provider_patcher.start()

    def tearDown(self):
        super()
        self.get_provider_patcher.stop()
        self.get_processor_patcher.stop()
        self.set_provider_patcher.stop()

    # pylint: disable=protected-access
    @patch.dict(
        environ, {"OTEL_RESOURCE_ATTRIBUTES": "service.name=my-test-service"}
    )
    def test_trace_init_default(self):
        _init_tracing({"zipkin": Exporter}, RandomIdGenerator, "test-version")

        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, Provider)
        self.assertIsInstance(provider.id_generator, RandomIdGenerator)
        self.assertIsInstance(provider.processor, Processor)
        self.assertIsInstance(provider.processor.exporter, Exporter)
        self.assertEqual(
            provider.processor.exporter.service_name, "my-test-service"
        )
        self.assertEqual(
            provider.resource.attributes.get("telemetry.auto.version"),
            "test-version",
        )

    @patch.dict(
        environ,
        {"OTEL_RESOURCE_ATTRIBUTES": "service.name=my-otlp-test-service"},
    )
    def test_trace_init_otlp(self):
        _init_tracing({"otlp": OTLPSpanExporter}, RandomIdGenerator)

        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, Provider)
        self.assertIsInstance(provider.id_generator, RandomIdGenerator)
        self.assertIsInstance(provider.processor, Processor)
        self.assertIsInstance(provider.processor.exporter, OTLPSpanExporter)
        self.assertIsInstance(provider.resource, Resource)
        self.assertEqual(
            provider.resource.attributes.get("service.name"),
            "my-otlp-test-service",
        )

    @patch.dict(environ, {OTEL_PYTHON_ID_GENERATOR: "custom_id_generator"})
    @patch("opentelemetry.sdk._configuration.IdGenerator", new=IdGenerator)
    @patch("opentelemetry.sdk.util.iter_entry_points")
    def test_trace_init_custom_id_generator(self, mock_iter_entry_points):
        mock_iter_entry_points.configure_mock(
            return_value=[
                IterEntryPoint("custom_id_generator", CustomIdGenerator)
            ]
        )
        id_generator_name = _get_id_generator()
        id_generator = _import_id_generator(id_generator_name)
        _init_tracing({}, id_generator)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider.id_generator, CustomIdGenerator)


class TestLoggingInit(TestCase):
    def setUp(self):
        self.processor_patch = patch(
            "opentelemetry.sdk._configuration.BatchLogRecordProcessor",
            DummyLogRecordProcessor,
        )
        self.provider_patch = patch(
            "opentelemetry.sdk._configuration.LoggerProvider",
            DummyLoggerProvider,
        )
        self.set_provider_patch = patch(
            "opentelemetry.sdk._configuration.set_logger_provider"
        )

        self.processor_mock = self.processor_patch.start()
        self.provider_mock = self.provider_patch.start()
        self.set_provider_mock = self.set_provider_patch.start()

    def tearDown(self):
        self.processor_patch.stop()
        self.set_provider_patch.stop()
        self.provider_patch.stop()
        root_logger = logging.getLogger("root")
        root_logger.handlers = [
            handler
            for handler in root_logger.handlers
            if not isinstance(handler, LoggingHandler)
        ]

    def test_logging_init_empty(self):
        _init_logging({}, "auto-version")
        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, DummyLoggerProvider)
        self.assertIsInstance(provider.resource, Resource)
        self.assertEqual(
            provider.resource.attributes.get("telemetry.auto.version"),
            "auto-version",
        )

    @patch.dict(
        environ,
        {"OTEL_RESOURCE_ATTRIBUTES": "service.name=otlp-service"},
    )
    def test_logging_init_exporter(self):
        _init_logging({"otlp": DummyOTLPLogExporter})
        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, DummyLoggerProvider)
        self.assertIsInstance(provider.resource, Resource)
        self.assertEqual(
            provider.resource.attributes.get("service.name"),
            "otlp-service",
        )
        self.assertIsInstance(provider.processor, DummyLogRecordProcessor)
        self.assertIsInstance(
            provider.processor.exporter, DummyOTLPLogExporter
        )
        logging.getLogger(__name__).error("hello")
        self.assertTrue(provider.processor.exporter.export_called)

    @patch.dict(
        environ,
        {"OTEL_RESOURCE_ATTRIBUTES": "service.name=otlp-service"},
    )
    @patch("opentelemetry.sdk._configuration._init_tracing")
    @patch("opentelemetry.sdk._configuration._init_logging")
    def test_logging_init_disable_default(self, logging_mock, tracing_mock):
        _initialize_components("auto-version")
        self.assertEqual(logging_mock.call_count, 0)
        self.assertEqual(tracing_mock.call_count, 1)

    @patch.dict(
        environ,
        {
            "OTEL_RESOURCE_ATTRIBUTES": "service.name=otlp-service",
            "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED": "True",
        },
    )
    @patch("opentelemetry.sdk._configuration._init_tracing")
    @patch("opentelemetry.sdk._configuration._init_logging")
    def test_logging_init_enable_env(self, logging_mock, tracing_mock):
        _initialize_components("auto-version")
        self.assertEqual(logging_mock.call_count, 1)
        self.assertEqual(tracing_mock.call_count, 1)


class TestMetricsInit(TestCase):
    def setUp(self):
        self.metric_reader_patch = patch(
            "opentelemetry.sdk._configuration.PeriodicExportingMetricReader",
            DummyMetricReader,
        )
        self.provider_patch = patch(
            "opentelemetry.sdk._configuration.MeterProvider",
            DummyMeterProvider,
        )
        self.set_provider_patch = patch(
            "opentelemetry.sdk._configuration.set_meter_provider"
        )

        self.metric_reader_mock = self.metric_reader_patch.start()
        self.provider_mock = self.provider_patch.start()
        self.set_provider_mock = self.set_provider_patch.start()

    def tearDown(self):
        self.metric_reader_patch.stop()
        self.set_provider_patch.stop()
        self.provider_patch.stop()

    def test_metrics_init_empty(self):
        _init_metrics({}, "auto-version")
        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, DummyMeterProvider)
        self.assertIsInstance(provider._sdk_config.resource, Resource)
        self.assertEqual(
            provider._sdk_config.resource.attributes.get(
                "telemetry.auto.version"
            ),
            "auto-version",
        )

    @patch.dict(
        environ,
        {"OTEL_RESOURCE_ATTRIBUTES": "service.name=otlp-service"},
    )
    def test_metrics_init_exporter(self):
        _init_metrics({"otlp": DummyOTLPMetricExporter})
        self.assertEqual(self.set_provider_mock.call_count, 1)
        provider = self.set_provider_mock.call_args[0][0]
        self.assertIsInstance(provider, DummyMeterProvider)
        self.assertIsInstance(provider._sdk_config.resource, Resource)
        self.assertEqual(
            provider._sdk_config.resource.attributes.get("service.name"),
            "otlp-service",
        )
        reader = provider._sdk_config.metric_readers[0]
        self.assertIsInstance(reader, DummyMetricReader)
        self.assertIsInstance(reader.exporter, DummyOTLPMetricExporter)


class TestExporterNames(TestCase):
    @patch.dict(
        environ,
        {
            "OTEL_TRACES_EXPORTER": _EXPORTER_OTLP,
            "OTEL_METRICS_EXPORTER": _EXPORTER_OTLP_PROTO_GRPC,
            "OTEL_LOGS_EXPORTER": _EXPORTER_OTLP_PROTO_HTTP,
        },
    )
    def test_otlp_exporter(self):
        self.assertEqual(
            _get_exporter_names("traces"), [_EXPORTER_OTLP_PROTO_GRPC]
        )
        self.assertEqual(
            _get_exporter_names("metrics"), [_EXPORTER_OTLP_PROTO_GRPC]
        )
        self.assertEqual(
            _get_exporter_names("logs"), [_EXPORTER_OTLP_PROTO_HTTP]
        )

    @patch.dict(
        environ,
        {
            "OTEL_TRACES_EXPORTER": _EXPORTER_OTLP,
            "OTEL_METRICS_EXPORTER": _EXPORTER_OTLP,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "grpc",
        },
    )
    def test_otlp_custom_exporter(self):
        self.assertEqual(
            _get_exporter_names("traces"), [_EXPORTER_OTLP_PROTO_HTTP]
        )
        self.assertEqual(
            _get_exporter_names("metrics"), [_EXPORTER_OTLP_PROTO_GRPC]
        )

    @patch.dict(
        environ,
        {
            "OTEL_TRACES_EXPORTER": _EXPORTER_OTLP_PROTO_HTTP,
            "OTEL_METRICS_EXPORTER": _EXPORTER_OTLP_PROTO_GRPC,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
        },
    )
    def test_otlp_exporter_conflict(self):
        # Verify that OTEL_*_EXPORTER is used, and a warning is logged
        with self.assertLogs(level="WARNING") as logs_context:
            self.assertEqual(
                _get_exporter_names("traces"), [_EXPORTER_OTLP_PROTO_HTTP]
            )
        assert len(logs_context.output) == 1

        with self.assertLogs(level="WARNING") as logs_context:
            self.assertEqual(
                _get_exporter_names("metrics"), [_EXPORTER_OTLP_PROTO_GRPC]
            )
        assert len(logs_context.output) == 1

    @patch.dict(environ, {"OTEL_TRACES_EXPORTER": "jaeger,zipkin"})
    def test_multiple_exporters(self):
        self.assertEqual(
            sorted(_get_exporter_names("traces")), ["jaeger", "zipkin"]
        )

    @patch.dict(environ, {"OTEL_TRACES_EXPORTER": "none"})
    def test_none_exporters(self):
        self.assertEqual(sorted(_get_exporter_names("traces")), [])

    def test_no_exporters(self):
        self.assertEqual(sorted(_get_exporter_names("traces")), [])

    @patch.dict(environ, {"OTEL_TRACES_EXPORTER": ""})
    def test_empty_exporters(self):
        self.assertEqual(sorted(_get_exporter_names("traces")), [])


class TestImportExporters(TestCase):
    def test_console_exporters(self):
        trace_exporters, metric_exporterts, logs_exporters = _import_exporters(
            ["console"], ["console"], ["console"]
        )
        self.assertEqual(
            trace_exporters["console"].__class__, ConsoleSpanExporter.__class__
        )
        self.assertEqual(
            logs_exporters["console"].__class__, ConsoleLogExporter.__class__
        )
        self.assertEqual(
            metric_exporterts["console"].__class__,
            ConsoleMetricExporter.__class__,
        )
