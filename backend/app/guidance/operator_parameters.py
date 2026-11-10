"""算子参数的单一类型来源；注册、生成、编译和执行共同消费。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, StringConstraints, model_validator

from app.guidance.model_ir import IndexedLinearModelSpec


Number = StrictInt | StrictFloat
DataValue = Number | Annotated[str, StringConstraints(pattern=r"^\$data\.\d+$")]
BasisNumber = Number | Literal["$candidate"]


class OperatorParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    def input_names(self, declared: set[str]) -> set[str]:
        return declared


class EmptyParameters(OperatorParameters):
    """不接受可调参数。"""


class MappingParameters(OperatorParameters):
    """从已存在的工件映射中选择一个路径或多个具名字段，不生成新特征。"""
    model_config = ConfigDict(json_schema_extra={"oneOf": [
        {"required": ["path"], "properties": {"path": {"type": "string"}, "fields": {"type": "null"}}},
        {"required": ["fields"], "properties": {"fields": {"type": "object"}, "path": {"type": "null"}}},
    ]})
    path: StrictStr | None = None
    fields: dict[str, StrictStr] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_projection(self):
        if (self.path is None) == (self.fields is None):
            raise ValueError("exactly one of path or fields is required")
        return self


class PathParameters(OperatorParameters):
    """按点分路径提取现有工件，不执行特征工程。"""
    path: StrictStr


class VectorParameters(PathParameters):
    """提取现有向量，并可按非负整数位置选择元素。"""
    indices: list[Annotated[int, Field(strict=True, ge=0)]] | None = Field(default=None, min_length=1)


class ScalarParameters(VectorParameters):
    """提取标量，或选择已有向量元素并求和。"""
    reducer: Literal["sum"] | None = None


class ConstantParameters(OperatorParameters):
    """返回显式声明的常量，不自动将假设变为原件事实。"""
    value: StrictInt | StrictFloat | StrictStr | StrictBool


class ExcelParameters(OperatorParameters):
    """读取首行表头的 Excel 工作表；支持选列、改名、前向填充与矩阵投影，不生成词特征。"""
    sheet: StrictInt | StrictStr = 0
    row_limit: Annotated[int, Field(strict=True, gt=0)] | None = None
    columns: list[StrictStr] = Field(default=[])
    rename: dict[str, StrictStr] = Field(default={})
    forward_fill: list[StrictStr] = Field(default=[])
    matrix_columns: list[StrictStr] = Field(default=[])
    matrix_name: StrictStr = "matrix"


class MomentsParameters(OperatorParameters):
    """在有限时间网格中按显式周期与窗口端点选择时刻。"""
    sample_count: StrictInt
    period: Annotated[int, Field(strict=True, gt=0)]
    window_end_offset: StrictInt
    fixed_moments: list[StrictInt] = Field(default=[])
    periodic_starts: list[StrictInt] = Field(default=[])


class GroupedRegressionParameters(OperatorParameters):
    """按表格列分组，以若干列的乘积作为过原点回归自变量。"""
    group_by: StrictStr
    response: StrictStr
    predictor_product: list[StrictStr]


class PlainBasis(OperatorParameters):
    kind: Literal["constant", "identity"]


class LocatedBasis(OperatorParameters):
    kind: Literal["hinge", "triangular"]
    location: BasisNumber


class CappedBasis(OperatorParameters):
    kind: Literal["capped_identity"]
    cap: BasisNumber


class ShiftedBasis(OperatorParameters):
    kind: Literal["shifted_ramp"]
    shift: BasisNumber


class CappedShiftedBasis(OperatorParameters):
    kind: Literal["capped_shifted_ramp"]
    cap: BasisNumber
    shift: BasisNumber = 0.0


class PeriodicBasis(OperatorParameters):
    kind: Literal["sin", "cos"]
    period: BasisNumber


class MaskBasis(OperatorParameters):
    kind: Literal["periodic_mask", "periodic_mask_times_x"]
    start: BasisNumber
    period: BasisNumber
    duration: BasisNumber


BasisTerm = Annotated[PlainBasis | LocatedBasis | CappedBasis | ShiftedBasis | CappedShiftedBasis | PeriodicBasis | MaskBasis, Field(discriminator="kind")]


class SearchParameters(OperatorParameters):
    candidates: list[Number] = Field(min_length=1)


class BasisRegressionParameters(OperatorParameters):
    """对声明的基函数进行最小二乘，可在显式网格上选择 $candidate。"""
    basis: list[BasisTerm] = Field(min_length=1)
    search: SearchParameters | None = None


class HingeParameters(OperatorParameters):
    """在声明的断点网格上比较连续折线与直线的 BIC。"""
    candidates: list[Number] = Field(min_length=1)
    support_threshold: Number = 2.0


class LeaveOneOutParameters(OperatorParameters):
    """对一元线性回归逐点留一复核系数，不接受现成模型作为输入。"""
    zero_intercept: StrictBool = False


class FutureForecastParameters(OperatorParameters):
    """以递增时间与目标向量进行滚动验证，比较趋势、周期和季节基线并预测未来区间。"""
    future_time: list[Number] = Field(min_length=1)
    candidate_periods: list[Number] = Field(min_length=1)
    season_length: StrictInt
    initial_train_size: StrictInt
    interval_level: Number
    sensitivity_threshold: Number = 0.2


class RobustForecastParameters(OperatorParameters):
    """按时间切分拟合稳健正则化回归，报告预测区间与泄漏检查。"""
    train_size: StrictInt
    alpha: Number
    huber_delta: Number
    interval_level: Number
    max_iterations: StrictInt = 100
    tolerance: Number = 1e-8
    sensitivity_threshold: Number = 0.1


class QuadraticExpression(OperatorParameters):
    quadratic: list[list[DataValue]]
    linear: list[DataValue]
    constant: DataValue = 0.0


class QuadraticConstraint(QuadraticExpression):
    upper_bound: DataValue


class DecisionBounds(OperatorParameters):
    lower: list[DataValue]
    upper: list[DataValue]


class LinearConstraints(OperatorParameters):
    a_ub: list[list[DataValue]] = Field(default=[])
    b_ub: list[DataValue] = Field(default=[])
    a_eq: list[list[DataValue]] = Field(default=[])
    b_eq: list[DataValue] = Field(default=[])


class QuadraticParameters(OperatorParameters):
    """在有限数据绑定下求有约束正定二次目标，使用显式多起点进行复核。"""
    starts: list[list[DataValue]]
    objective: QuadraticExpression
    bounds: DecisionBounds
    linear_constraints: LinearConstraints = Field(default=LinearConstraints())
    quadratic_constraints: list[QuadraticConstraint] = Field(default=[])
    constraint_tolerance: Number = 1e-7
    multistart_tolerance: Number = 1e-6
    sensitivity_threshold: Number = 1e-4


class CoefficientProvenance(OperatorParameters):
    context: StrictStr
    parameter_id: StrictStr
    coordinates: list[StrictStr | StrictInt]
    value: Number
    multiplier: Number
    source_ref: StrictStr


class CompiledLinearModel(OperatorParameters):
    objective_sense: Literal["minimize", "maximize"]
    variable_labels: list[StrictStr]
    objective: list[Number]
    integrality: list[StrictInt]
    lower_bounds: list[Number | None]
    upper_bounds: list[Number | None]
    constraint_labels: list[StrictStr]
    matrix: list[list[Number]]
    constraint_lower: list[Number | None]
    constraint_upper: list[Number | None]
    provenance: list[CoefficientProvenance]


class MilpParameters(OperatorParameters):
    """仅由编译器构造的 MILP 参数；模型应声明 indexed_linear_model，不直接调用本算子。"""
    model_config = ConfigDict(json_schema_extra={"oneOf": [
        {"required": ["compiled_model"], "properties": {"compiled_model": {"type": "object"}, "indexed_model_template": {"type": "null"}}},
        {"required": ["indexed_model_template"], "properties": {"indexed_model_template": {"type": "object"}, "compiled_model": {"type": "null"}}},
    ]})
    compiled_model: CompiledLinearModel | None = None
    indexed_model_template: IndexedLinearModelSpec | None = None

    @model_validator(mode="after")
    def require_one_model(self):
        if (self.compiled_model is None) == (self.indexed_model_template is None):
            raise ValueError("exactly one compiler-owned compiled_model or indexed_model_template is required")
        return self

    def input_names(self, declared: set[str]) -> set[str]:
        if self.indexed_model_template is None:
            return declared
        return {item.id for item in self.indexed_model_template.parameters if item.runtime_source is not None}


class DecisionParameters(OperatorParameters):
    """对收益/成本指标做加权排序，采用给定或熵权并复核权重扰动。"""
    criterion_types: list[Literal["benefit", "cost"]]
    weight_method: Literal["provided", "entropy"]
    weights: list[Number] = Field(default=[])
    perturbation_fraction: Number = 0.05
    stability_threshold: Number = 0.8


class DynamicsParameters(OperatorParameters):
    """从时间与状态矩阵估计仿射 ODE，用 RK4、边界与守恒声明复核。"""
    include_offset: StrictBool = True
    max_condition_number: Number = 1e10
    integration_substeps: StrictInt = 1
    lower_bounds: list[Number] = Field(default=[])
    upper_bounds: list[Number] = Field(default=[])
    boundary_tolerance: Number = 1e-8
    conservation_vectors: list[list[Number]] = Field(default=[])
    conservation_tolerance: Number = 1e-8
    step_sensitivity_threshold: Number = 1e-3


class GraphParameters(OperatorParameters):
    """对显式端点和权重/容量矩阵计算最短路与最大流。"""
    endpoints: list[list[Number]]
    node_count: StrictInt
    source: StrictInt
    sink: StrictInt


class PlotParameters(OperatorParameters):
    """将已有观测与拟合序列画图并绑定结果 ID，不生成或证明数值结论。"""
    path: StrictStr
    title: StrictStr = "Observed and fitted"
    x_label: StrictStr = "x"
    y_label: StrictStr = "y"
    dpi: StrictInt = 150
    role: StrictStr = "result_fit"
    linked_result_ids: list[StrictStr] = Field(default=[])
