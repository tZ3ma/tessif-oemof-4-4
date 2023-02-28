"""Wrapping the tessif-oemof post-processing."""
from collections import abc, defaultdict

import numpy as np
import pandas as pd
import tessif.post_process as base
from oemof import solph
from tessif.frused import namedtuples as nts
from tessif.frused.defaults import energy_system_nodes as esn_defs


class OmfResultier(base.Resultier):
    """Transform nodes and edges into their name representation. Child of
    :class:`~tessif.transform.es2mapping.base.Resultier` and mother of all
    oemof Resultiers.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.
    """

    component_type_mapping = {
        solph.components.GenericStorage: "storage",
        solph.components.ExtractionTurbineCHP: "transformer",
        solph.components.GenericCHP: "transformer",
        solph.components.OffsetTransformer: "transformer",
        solph.custom.Link: "connector",
        solph.network.Bus: "bus",
        solph.network.Sink: "sink",
        solph.network.Source: "source",
        solph.network.Transformer: "transformer",
    }

    def __init__(self, optimized_es, **kwargs):

        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_nodes(self, optimized_es):
        r"""Return list of node label string representations."""
        return [str(node.label) for node in optimized_es.nodes]

    def _map_node_uids(self, optimized_es):
        """Return a list of node uids."""
        _uid_nodes = dict()
        for node in optimized_es.nodes:
            prelim_uid = node.label
            if prelim_uid.component is None:
                uid_dict = prelim_uid._asdict()
                uid_dict["component"] = OmfResultier.component_type_mapping[type(node)]
                uid = nts.Uid(**uid_dict)
            else:
                uid = prelim_uid

            _uid_nodes[str(node.label)] = uid

        return _uid_nodes

    def _map_edges(self, optimized_es):
        r"""Return list of (inflow, node) label string representation."""
        return [
            nts.Edge(str(inflow), str(node))
            for node in optimized_es.nodes
            for inflow in node.inputs.keys()
        ]

    def dct_repr(self):
        """Extend the base dict reprsentation."""
        excluded = [
            "_inflow_characterized_components",
            "_outflow_characterized_components",
        ]
        dct = {
            key: value for key, value in self.__dict__.items() if key not in excluded
        }
        return dct


class IntegratedGlobalResultier(OmfResultier, base.IntegratedGlobalResultier):
    """Extracting the integrated global results out of the energy system and
    conveniently aggregating them (rounded to unit place) inside a dictionairy
    keyed by result name.

    Integrated global results (IGR) mapped by result name.

    Integrated global results currently consist of meta and non-meta
    results. the **meta** results are handled by the :mod:`~tessif.analyze`
    module (see :attr:`tessif.analyze.Comparatier.integrated_global_results`)
    and consist of:

        - ``time``
        - ``memory``

    results. whereas the **non-meta** results usually consist of:

        - ``emissions``
        - ``costs``

    results, which are handled here. Tessif's energy system, however, allow to
    formulate a number of
    :attr:`~tessif.model.energy_system.AbstractEnergySystem.global_constraints`
    which then would automatically be post processed here.

    The befornamed strings serve as key insidethe mapping.

    Note
    ----
    In regard to global constraints (mainly emissions), Oemof calls these
    :func:`~oemof.solph.constraints.generic_integral_limit`. Labeling them as
    ``global_constraints`` and therfor generating results the
    :class:`IntegratedGlobalResultier` can extract, is only done if one of the
    two folloing conditions are met:

        1. A :mod:`tessif energy system <tessif.model.energy_system>` is used
           and beeing :meth:`transformed into an oemof energy system
           <tessif.transform.es2es.omf.transform>`.

        2. A native :class:`oemof energy system
           <oemof.core.energy_system.EnergySystem>` is used in conjunction with
           :meth:`tessif.simulate.omf_from_es` provided the constraints are
           defined using a dictionairy as in
           :attr:`tessif.model.energy_system.AbstractEnergySystem.global_constraints`

           Meaing if you have got an energy system object in ``es`` adding an
           emission constraint would look like::

               es.global_constraints = {'emissions': 42}


            See the :ref:`following example <es2mapping_omf_nativeConstraints>`

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.IntegratedGlobalResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_global_results(self, optimized_es):

        flow_results = FlowResultier(optimized_es)
        cap_results = CapacityResultier(optimized_es)

        total_emissions = 0.0
        flow_costs = 0.0

        capital_costs = 0.0

        for edge in self.edges:
            net_energy_flow = flow_results.edge_net_energy_flow[edge]
            specific_emissions = flow_results.edge_specific_emissions[edge]
            specific_flow_costs = flow_results.edge_specific_flow_costs[edge]

            total_emissions += net_energy_flow * specific_emissions

            flow_costs += net_energy_flow * specific_flow_costs

        for node in self.nodes:
            initial_capacity = cap_results.node_original_capacity[node]
            final_capacity = cap_results.node_installed_capacity[node]
            expansion_cost = cap_results.node_expansion_costs[node]

            if not any([cap is None for cap in (final_capacity, initial_capacity)]):
                node_expansion_costs = (
                    final_capacity - initial_capacity
                ) * expansion_cost
            else:
                node_expansion_costs = 0

            if isinstance(initial_capacity, pd.Series):
                node_expansion_costs = sum(node_expansion_costs)

            capital_costs += node_expansion_costs

        return {
            "emissions (sim)": round(total_emissions, 0),
            "costs (sim)": round(
                optimized_es.results["global"]["costs"],
                0,
            ),
            "opex (ppcd)": round(flow_costs, 0),
            "capex (ppcd)": round(capital_costs, 0),
        }

        return optimized_es.results["global"]


class ScaleResultier(OmfResultier, base.ScaleResultier):
    """Extract number of constraints.

    Parameters
    ----------
    optimized_es:
        :ref:`Model <SupportedModels>` specific, optimized energy system
        containing its results.

    See Also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.post_process.ScaleResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_number_of_constraints(self, optimized_es):
        """Interface to extract the number of constraints out of the
        optimized oemof system model.
        """
        return optimized_es.results.problem.number_of_constraints


class LoadResultier(OmfResultier, base.LoadResultier):
    """
    Transforming flow results into dictionairies keyed by node.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See Also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.LoadResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_loads(self, optimized_es):
        """Map loads to node labels."""
        # Use defaultdict of empty DataFrame as loads container:
        _loads = defaultdict(lambda: pd.DataFrame())

        for node in optimized_es.nodes:
            time_series_results = solph.views.node(
                optimized_es.results["main"], node
            ).get("sequences", pd.DataFrame())

            # only keep columns with 'flow' results
            time_series_results = time_series_results[
                np.array(
                    [col for col in time_series_results.columns if "flow" == col[1]],
                    dtype=object,
                )
            ]

            # rename time_series_results to to edge labels
            time_series_results.rename(
                columns={
                    col: (str(col[0][0].label), str(col[0][1].label))
                    for col in time_series_results.columns
                },
                inplace=True,
            )

            temp_df = time_series_results.copy()
            # rename time_series_results inflow columns to inflow node name
            time_series_results.rename(
                columns={
                    col: n
                    for col in time_series_results.columns
                    for n in col
                    if not any(str(x.label) == col[1] for x in node.outputs.keys())
                    and n != str(node.label)
                },
                inplace=True,
            )

            inflows = time_series_results.drop(
                columns=[
                    col for col in time_series_results.columns if isinstance(col, tuple)
                ]
            )

            # make inflow values negative
            inflows = inflows.multiply(-1)
            # enforce -0. on inflows
            inflows = inflows.replace({0: -float(0), float(0): -float(0)})

            outflows = time_series_results.drop(
                columns=[
                    col
                    for col in time_series_results.columns
                    if not isinstance(col, tuple)
                ]
            )

            # enforce +0. on outflows
            outflows = outflows.replace({-float(0): float(0)})

            # rename time_series_results out columns to outflow node name
            outflows.rename(
                columns={
                    col: n
                    for col in temp_df.columns
                    for n in col
                    if not any(str(x.label) == col[0] for x in node.inputs.keys())
                    and n != str(node.label)
                },
                inplace=True,
            )

            time_series_results = pd.concat([inflows, outflows], axis="columns")
            time_series_results.columns.name = str(node.label)
            _loads[str(node.label)] = time_series_results

        return dict(_loads)


class CapacityResultier(base.CapacityResultier, LoadResultier):
    """Transforming installed capacity results dictionairies keyed by node.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.CapacityResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):

        self._inflow_characterized_components = (solph.Sink,)

        self._outflow_characterized_components = (
            solph.Transformer,
            solph.Source,
            solph.components.OffsetTransformer,
            solph.components.ExtractionTurbineCHP,
            solph.components.GenericCHP,
        )

        super().__init__(optimized_es=optimized_es, **kwargs)

    @property
    def node_characteristic_value(self):
        r"""Map node label to characteristic value.

        Components of variable size have a characteristic value of ``None``.

        Characteristic value in this context means:

            - :math:`cv = \frac{\text{sum}\left(\text{characteristic flow}
              \right)}{\text{installed capacity}}` for:

                - :class:`~oemof.solph.network.Source` objects
                - :class:`~oemof.solph.network.Sink` objects
                - :class:`~oemof.solph.network.Transformer` objects
                - :class:`~oemof.solph.components.ExtractionTurbineCHP` objects
                - :class:`~oemof.solph.components.GenericCHP` objects
                - :class:`~oemof.solph.components.OffsetTransformer` objects

            - :math:`cv = \frac{\text{sum}\left(\text{SOC}\right)}
              {\text{capacity}}` for:

                - :class:`~oemof.solph.components.GenericStorage`


        Characteristic flow in this context means:

            - :math:`\text{sum}\left(\text{all flows}\right)` for:

                - :class:`~oemof.solph.network.Source` objects
                - :class:`~oemof.solph.network.Sink` objects

            - :math:`\text{sum}\left(\text{outflow}\right)` for:

                - :class:`~oemof.solph.components.OffsetTransformer` objects

            - :math:`\text{sum}\left(0\text{th outflow}\right)` for:

                - :class:`~oemof.solph.network.Transformer` objects
                - :class:`~oemof.solph.components.ExtractionTurbineCHP` objects

            - :math:`\text{sum}\left(\text{power outflow}\right)` for:

                - :class:`~oemof.solph.components.GenericCHP` objects

        The **node fillsize** of :ref:`integrated component results graphs
        <Integrated_Component_Results>` scales with the
        **characteristic value**.
        If no capacity is defined (i.e for nodes of variable size, like busses
        or excess sources and sinks, node size is set to it's default (
        :attr:`nxgrph_visualize_defaults[node_fill_size]
        <tessif.frused.defaults.nxgrph_visualize_defaults>`).

        Note
        ----
        This specific property is overridden to provide a more oemof-specific
        description on what is deemed a ``characteristic_value``.
        """
        return self._characteristic_values

    def _map_installed_capacities(self, optimized_es):
        """Map installed capacities to node labels. None for nodes of variable
        size"""

        # Use default dict as installed capacities container:
        _installed_capacities = defaultdict(float)

        for node in optimized_es.nodes:

            node_inst_cap_dict = dict()
            # map inflow characterized nodes:
            if isinstance(node, self._inflow_characterized_components):
                for inflow in node.inputs.keys():
                    if (inflow, node) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(inflow, node)], "investment"
                        )
                        # is inflow -> node characterized by investment?
                        if inv_obj:

                            # yes, so get the results sub dict holding it ...
                            scalar_results = solph.views.node(
                                optimized_es.results["main"], node
                            ).get("scalars", dict())

                            # ... extract the value
                            # ... and add start value
                            inst_cap = (
                                scalar_results.get(((inflow, node), "invest"), 0)
                                + inv_obj.existing
                            )

                        else:
                            # Extract nominal_value if present
                            inst_cap = getattr(
                                optimized_es.flows()[(inflow, node)],
                                "nominal_value",
                                esn_defs["variable_capacity"],
                            )

                            # or the inst cap is inferred by using the
                            # max inflow
                            if inst_cap == esn_defs["variable_capacity"]:
                                inst_cap = max(
                                    self.node_inflows[str(node.label)][
                                        str(inflow.label)
                                    ]
                                )
                        node_inst_cap_dict[str(inflow.label)] = inst_cap

                if len(node.inputs.keys()) > 1:
                    # distinguish between multiple inflows characterized nodes
                    inst_cap = pd.Series(node_inst_cap_dict)
                else:
                    # and singular ones
                    inst_cap = tuple(node_inst_cap_dict.values())[0]

                _installed_capacities[str(node.label)] = inst_cap

            elif isinstance(node, self._outflow_characterized_components):
                for outflow in node.outputs.keys():
                    if (node, outflow) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(node, outflow)], "investment"
                        )

                        if inv_obj:
                            # is node -> outflow characterized by investment?

                            # yes, so get the results sub dict holding it ...
                            scalar_results = solph.views.node(
                                optimized_es.results["main"], node
                            ).get("scalars", dict())

                            # ... extract the value
                            # ... and add start value
                            inst_cap = (
                                scalar_results.get(((node, outflow), "invest"), 0)
                                + inv_obj.existing
                            )

                        else:
                            # No, so nominal value is the installed capacity ..
                            inst_cap = getattr(
                                optimized_es.flows()[(node, outflow)],
                                "nominal_value",
                                esn_defs["variable_capacity"],
                            )

                            # or the inst cap is inferred by using the
                            # max outflow
                            if inst_cap == esn_defs["variable_capacity"]:
                                outflow_series = self.node_outflows[str(node.label)][
                                    str(outflow.label)
                                ]
                                # inst_cap = max(outflow_series)
                                # account for unused storages
                                if not outflow_series.empty:
                                    inst_cap = max(outflow_series)
                                else:
                                    inst_cap = 0

                        node_inst_cap_dict[str(outflow.label)] = inst_cap

                if len(node.outputs.keys()) > 1:
                    # distinguish between multiple outflows characterized
                    # nodes
                    inst_cap = pd.Series(node_inst_cap_dict)
                else:
                    # and singular ones
                    inst_cap = tuple(node_inst_cap_dict.values())[0]

                _installed_capacities[str(node.label)] = inst_cap

            elif isinstance(node, (solph.Bus, solph.custom.Link)):
                _installed_capacities[str(node.label)] = esn_defs["variable_capacity"]

            elif isinstance(node, solph.components.GenericStorage):

                if node.investment:

                    additional_capacity = solph.views.node(
                        optimized_es.results["main"], node
                    ).get("scalars")[((node, None), "invest")]

                    existing_capacity = node.investment.existing

                    _installed_capacities[str(node.label)] = (
                        additional_capacity + existing_capacity
                    )

                else:
                    _installed_capacities[
                        str(node.label)
                    ] = node.nominal_storage_capacity

        return dict(_installed_capacities)

    def _map_original_capacities(self, optimized_es):
        """Map pre-optimized installed capacities to node labels.
        tessif.frused.esn_defs['variable_capacity'] for
        nodes of variable size"""

        # Use default dict as installed capacities container:
        _installed_capacities = defaultdict(float)

        for node in optimized_es.nodes:

            node_inst_cap_dict = dict()
            # map inflow characterized nodes:
            if isinstance(node, self._inflow_characterized_components):
                for inflow in node.inputs.keys():
                    if (inflow, node) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(inflow, node)], "investment"
                        )
                        # is inflow -> node characterized by investment?
                        if inv_obj:
                            inst_cap = inv_obj.existing

                        else:
                            # Extract nominal_value if present
                            inst_cap = getattr(
                                optimized_es.flows()[(inflow, node)],
                                "nominal_value",
                                esn_defs["variable_capacity"],
                            )

                            # or the inst cap is set to 0 if no value was set
                            # an thus a fallback on the default occurred
                            if inst_cap == esn_defs["variable_capacity"]:
                                inst_cap = 0

                        node_inst_cap_dict[str(inflow.label)] = inst_cap

                if len(node.inputs.keys()) > 1:
                    # distinguish between multiple inflows characterized nodes
                    inst_cap = pd.Series(node_inst_cap_dict)
                else:
                    # and singular ones
                    inst_cap = tuple(node_inst_cap_dict.values())[0]

                _installed_capacities[str(node.label)] = inst_cap

            elif isinstance(node, self._outflow_characterized_components):
                for outflow in node.outputs.keys():
                    if (node, outflow) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(node, outflow)], "investment"
                        )

                        if inv_obj:
                            # is node -> outflow characterized by investment?
                            inst_cap = inv_obj.existing

                        else:
                            # No, so nominal value is the installed capacity ..
                            inst_cap = getattr(
                                optimized_es.flows()[(node, outflow)],
                                "nominal_value",
                                esn_defs["variable_capacity"],
                            )

                            # or the inst cap is set to 0 if no value was set
                            # an thus a fallback on the default occurred
                            if inst_cap == esn_defs["variable_capacity"]:
                                inst_cap = 0

                        node_inst_cap_dict[str(outflow.label)] = inst_cap

                if len(node.outputs.keys()) > 1:
                    # distinguish between multiple outflows characterized nodes
                    inst_cap = pd.Series(node_inst_cap_dict)
                else:
                    # and singular ones
                    inst_cap = tuple(node_inst_cap_dict.values())[0]

                _installed_capacities[str(node.label)] = inst_cap

            elif isinstance(node, (solph.Bus, solph.custom.Link)):
                _installed_capacities[str(node.label)] = esn_defs["variable_capacity"]

            elif isinstance(node, solph.components.GenericStorage):

                if node.investment:
                    inst_cap = node.investment.existing

                else:
                    inst_cap = node.nominal_storage_capacity

                _installed_capacities[str(node.label)] = inst_cap

        return dict(_installed_capacities)

    def _map_expansion_costs(self, optimized_es):
        expansion_costs = dict()

        # Map the respective expansion costs:
        for node in optimized_es.nodes:

            node_expansion_costs_dict = dict()
            # map inflow characterized nodes:
            if isinstance(node, self._inflow_characterized_components):
                for inflow in node.inputs.keys():
                    if (inflow, node) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(inflow, node)], "investment"
                        )
                        # is inflow -> node characterized by investment?
                        if inv_obj:
                            cost = inv_obj.ep_costs

                        else:
                            cost = esn_defs["expansion_costs"]

                        node_expansion_costs_dict[str(inflow.label)] = cost

                if len(node.inputs.keys()) > 1:
                    # distinguish between multiple inflows characterized nodes
                    inst_cap = pd.Series(node_expansion_costs_dict)
                else:
                    # and singular ones
                    inst_cap = tuple(node_expansion_costs_dict.values())[0]

                expansion_costs[str(node.label)] = inst_cap

            elif isinstance(node, self._outflow_characterized_components):
                for outflow in node.outputs.keys():
                    if (node, outflow) in optimized_es.flows():
                        # parse investment objects
                        inv_obj = getattr(
                            optimized_es.flows()[(node, outflow)], "investment"
                        )

                        if inv_obj:
                            # is node -> outflow characterized by investment?
                            cost = inv_obj.ep_costs
                        else:
                            cost = esn_defs["expansion_costs"]

                        node_expansion_costs_dict[str(outflow.label)] = cost

                if len(node.outputs.keys()) > 1:
                    # distinguish between multiple outflows characterized nodes
                    costs = pd.Series(node_expansion_costs_dict)
                else:
                    # and singular ones
                    costs = tuple(node_expansion_costs_dict.values())[0]

                expansion_costs[str(node.label)] = costs

            elif isinstance(node, solph.components.GenericStorage):

                if node.investment:
                    costs = node.investment.ep_costs

                else:
                    costs = esn_defs["expansion_costs"]

                expansion_costs[str(node.label)] = costs

            else:
                expansion_costs[str(node.label)] = esn_defs["expansion_costs"]

        return expansion_costs

    def _map_characteristic_values(self, optimized_es):
        """Map node label to characteristic value."""

        # Use default dict as capacity factors container:
        _characteristic_values = defaultdict(float)

        # Map the respective capacity factors:
        for node in optimized_es.nodes:

            characteristic_mean = pd.Series(dtype="float64")

            inst_cap = self._installed_capacities[str(node.label)]

            # is the installed capacity a singular value?
            if not isinstance(inst_cap, abc.Iterable):

                # yes, it is
                if inst_cap != esn_defs["variable_capacity"]:

                    if isinstance(node, solph.components.GenericCHP):
                        char_tar = list(node.electrical_output)[0]
                        characteristic_mean = self._outflows[str(node.label)][
                            str(char_tar)
                        ].mean(axis="index")

                    elif isinstance(node, solph.components.GenericStorage):
                        characteristic_mean = (
                            StorageResultier(optimized_es)
                            .node_soc[str(node.label)]
                            .mean(axis="index")
                        )

                    # map all other nodes
                    else:
                        characteristic_mean = self.node_summed_loads[
                            str(node.label)
                        ].mean(axis="index")

                    # deal with node of variable size, left unused:
                    if inst_cap == 0:
                        _characteristic_values[str(node.label)] = 0
                    else:
                        _characteristic_values[str(node.label)] = (
                            characteristic_mean / inst_cap
                        )
                    # print(characteristic_mean, inst_cap)

                else:
                    _characteristic_values[str(node.label)] = esn_defs[
                        "characteristic_value"
                    ]

            # not its not, so keep the series format
            else:

                characteristic_mean = self._outflows[str(node.label)].mean()

                # create the series beforehand
                char_values = pd.Series(dtype="float64")
                for idx, cap in inst_cap.fillna(0).items():

                    if cap != 0:
                        char_values[idx] = characteristic_mean[idx] / cap
                    else:
                        char_values[idx] = 0

                # to replace nans by 0:
                _characteristic_values[str(node.label)] = char_values.fillna(0)

        return dict(_characteristic_values)


class StorageResultier(OmfResultier, base.StorageResultier):
    r"""Transforming storage results into dictionairies keyed by node.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.StorageResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_states_of_charge(self, optimized_es):
        """Map storage labels to their states of charge"""

        _socs = defaultdict(lambda: pd.Series(dtype="float64"))
        for node in optimized_es.nodes:
            if isinstance(node, solph.components.GenericStorage):
                soc = solph.views.node(optimized_es.results["main"], node).get(
                    "sequences"
                )[((node, None), "storage_content")]
                soc.name = str(node.label)

                _socs[str(node.label)] = soc

        return dict(_socs)


class NodeCategorizer(OmfResultier, base.NodeCategorizer):
    r"""Categorizing the nodes of an optimized oemof energy system.

    Categorization utilizes :attr:`~tessif.frused.namedtuples.Uid`.

    Nodes are categorized by:

        - Energy :paramref:`component
          <tessif.frused.namedtuples.Uid.component>`
          (One of the  'Bus', 'Sink', etc..)

        - Energy :paramref:`sector <tessif.frused.namedtuples.Uid.sector>`
          ('power', 'heat', 'mobility', 'coupled')

        - :paramref:`Region <tessif.frused.namedtuples.Uid.region>`
          ('arbitrary label')

        - :paramref:`Coordinates <tessif.frused.namedtuples.Uid.latitude>`
          (latitude, longitude in degree)

        - Energy :paramref:`carrier <tessif.frused.namedtuples.Uid.carrier>`
          ('solar', 'wind', 'electricity', 'steam' ...)

        - :paramref:`Node type <tessif.frused.namedtuples.Uid.node_type>`
          ('arbitrary label')

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.NodeCategorizer>`.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_node_components(self, optimized_es):
        """Nodes ordered by component "Bus" "Sink" etc.."""

        # Use default dict as sector strings container
        _component_nodes = defaultdict(list)

        # Map the respective sectors:
        for node in optimized_es.nodes:
            if hasattr(node.label, "component"):
                _component_nodes[node.label.component.lower().capitalize()].append(
                    str(node.label)
                )
            # Node has no component attributed in node.label
            else:
                _component_nodes["Unspecified"].append(str(node.label))

        return dict(_component_nodes)

    def _map_node_sectors(self, optimized_es):
        """Nodes ordered by sector. i.e "Power" "Heat" "Mobility" "Coupled"."""

        # Use default dict as sector strings container
        _sectored_nodes = defaultdict(list)

        # Map the respective sectors:
        for node in optimized_es.nodes:
            if hasattr(node.label, "sector"):
                _sectored_nodes[node.label.sector].append(str(node.label))
            # Node has no sector attributed in node.label
            else:
                _sectored_nodes["Unspecified"].append(str(node.label))

        return dict(_sectored_nodes)

    def _map_node_regions(self, optimized_es):
        """Nodes ordered by region. i.e "World" "South" "Antinational"."""

        # Use default dict as sector strings container
        _regionalized_nodes = defaultdict(list)

        # Map the respective sectors:
        for node in optimized_es.nodes:
            if hasattr(node.label, "region"):
                _regionalized_nodes[node.label.region].append(str(node.label))
            # Node has no region attributed in node.label
            else:
                _regionalized_nodes["Unspecified"].append(str(node.label))

        return dict(_regionalized_nodes)

    def _map_node_coordinates(self, optimized_es):
        """Longitude and Latitude of each node present in energy system."""

        # Use default dict as coordinate namedtuple container
        _coordinates = defaultdict(nts.Coordinates)

        # 3.) Map the respective coordinates:
        for node in optimized_es.nodes:
            if hasattr(node.label, "latitude") and hasattr(node.label, "latitude"):
                _coordinates[str(node.label)] = nts.Coordinates(
                    node.label.latitude, node.label.longitude
                )

            # Node has no coordinates attributed in node.label
            else:
                _coordinates[str(node.label)] = nts.Coordinates(None, None)

        return dict(_coordinates)

    def _map_node_energy_carriers(self, optimized_es):
        """Nodes ordered by energy carrier. "Electricity", "Gas", "Heat"."""

        # Use default dict as carrier strings container
        _carrier_grouped_nodes = defaultdict(list)
        _node_energy_carriers = defaultdict(str)

        # Map the respective carriers:
        for node in optimized_es.nodes:
            if hasattr(node.label, "carrier"):
                _carrier_grouped_nodes[node.label.carrier].append(str(node.label))
                _node_energy_carriers[str(node.label)] = node.label.carrier

            # Node has no region attributed in node.label
            else:
                _carrier_grouped_nodes["Unspecified"].append(str(node.label))
                _node_energy_carriers[str(node.label)] = "Unspecified"

        return (dict(_carrier_grouped_nodes), dict(_node_energy_carriers))

    def _map_node_types(self, optimized_es):
        """Nodes grouped by "type" (arbitrary classification)"""

        # Use default dict as sector strings container
        _typed_nodes = defaultdict(list)

        # Map the respective sectors:
        for node in optimized_es.nodes:
            if hasattr(node.label, "node_type"):
                _typed_nodes[node.label.node_type].append(str(node.label))
            # Node has no sector attributed in node.label
            else:
                _typed_nodes["Unspecified"].append(str(node.label))

        return dict(_typed_nodes)


class FlowResultier(base.FlowResultier, LoadResultier):
    """
    Transforming flow results into dictionairies keyed by edges.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.FlowResultier>`.
    """

    def __init__(self, optimized_es, **kwargs):

        super().__init__(optimized_es=optimized_es, **kwargs)

    def _map_specific_flow_costs(self, optimized_es):
        r"""Energy specific flow costs mapped to edges."""
        # Use default dict as net energy flows container:
        _specific_flow_costs = defaultdict(float)

        # Map the respective flow costs:
        for node in optimized_es.nodes:
            for inflow in node.inputs.keys():
                _specific_flow_costs[
                    nts.Edge(str(inflow.label), str(node.label))
                ] = getattr(optimized_es.flows()[(inflow, node)], "variable_costs", 0)[
                    0
                ]

        return dict(_specific_flow_costs)

    def _map_specific_emissions(self, optimized_es):
        r"""Energy specific emissions mapped to edges."""
        # Use default dict as net energy flows container:
        _specific_emissions = defaultdict(float)

        # Map the respective capacity factors:
        for node in optimized_es.nodes:
            for inflow in node.inputs.keys():
                _specific_emissions[
                    nts.Edge(str(inflow.label), str(node.label))
                ] = getattr(optimized_es.flows()[(inflow, node)], "emissions", 0)

        return dict(_specific_emissions)


class AllResultier(CapacityResultier, FlowResultier, StorageResultier):
    r"""
    Transforming energy system results into a dictionary keyed by attribute.
    Incorporates all the functionalities from its bases.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    Note
    ----
    This class allows interfacing with **ALL** framework processing utilities.
    It extracts every bit of info the author ever needed in his postprocessing.

    It is meant to be a "one fits all" solution.
    Perfectly fit for showing "proof of concepts" or debugging energy system
    components.

    **Potentially Unfit For Large System Models**.
    """

    def __init__(self, optimized_es, **kwargs):
        super().__init__(optimized_es=optimized_es, **kwargs)


class LabelFormatier(base.LabelFormatier, FlowResultier, CapacityResultier):
    r"""
    Generate component summaries as multiline label dictionairy entries.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.LabelFormatier>`.
    """

    def __init__(self, optimized_es, **kwargs):

        super().__init__(optimized_es=optimized_es, **kwargs)


class NodeFormatier(base.NodeFormatier, CapacityResultier):
    r"""Transforming energy system results into node visuals.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    cgrp: str, default='name'
        Which group of color attribute(s) to return. One of::

            {'name', 'carrer', 'sector'}

        Color related attributes are grouped by
        :class:`tessif.frused.namedtuples.NodeColorGroupings` and are thus
        returned as a :class:`typing.NamedTuple`. Certain api functionalities
        expect those attributes to be dicts. (Usually those working only
        on :class:`~tessif.transform.es2mapping.base.ESTransformer` input).
        Use this parameter on Formatier creation to provide the expected
        dictionairy.

    drawutil: str, default='nx'
        Which drawuing utility backend to format node size, fil_size and
        shape to. ``'dc'`` for :mod:`plotly-dash-cytoscape
        <tessif.visualize.dcgrph>` or ``'nx'`` for
        :mod:`networkx-matplotlib <tessif.visualize.nxgrph>`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.NodeFormatier>`.
    """

    def __init__(self, optimized_es, cgrp="name", drawutil="nx", **kwargs):

        super().__init__(
            optimized_es=optimized_es, cgrp=cgrp, drawutil=drawutil, **kwargs
        )


class MplLegendFormatier(base.MplLegendFormatier, CapacityResultier):
    r"""
    Generating visually enhanced matplotlib legends for nodes and edges.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    cgrp: str, default='name'
        Which group of color attribute(s) to return. One of::

            {'name', 'carrer', 'sector'}

        Color related attributes are grouped by
        :class:`tessif.frused.namedtuples.NodeColorGroupings` and are thus
        returned as a :class:`typing.NamedTuple`. Certain api functionalities
        expect those attributes to be dicts. (Usually those working only
        on :class:`~tessif.transform.es2mapping.base.ESTransformer` input).
        Use this parameter on Formatier creation to provide the expected
        dictionairy.

    markers: str, default='formatier'
        What marker to use for legend entries. Either ``'formatier'`` or
        one of the :any:`matplotlib.markers`.

        If ``'formatier'`` is used, markers will be inferred from
        :attr:`NodeFormatier.node_shape`.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.MplLegendFormatier>`.
    """

    def __init__(self, optimized_es, cgrp="all", markers="formatier", **kwargs):

        # needed transformers

        # mpl legend formatier is the only class needing an extra formatier
        # instead of just inheriting it. This allows bundling as done in the
        # AllFormatier with its specific color group (cgrp) and still be able
        # to map the legends for all colors

        # a different plausible approach would be to only map the bundled
        # color, and implement some if clauses to only map the legend
        # requested. This also implies chaining the behaviour of
        # MplLegendFormatier.node_legend
        self._nformats = NodeFormatier(optimized_es, drawutil="nx", cgrp="all")

        super().__init__(
            optimized_es=optimized_es, cgrp="all", markers=markers, **kwargs
        )


class EdgeFormatier(base.EdgeFormatier, FlowResultier):
    r"""Transforming energy system results into edge visuals.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    drawutil: str, default='nx'
        Which drawuing utility backend to format node size, fil_size and
        shape to. ``'dc'`` for :mod:`plotly-dash-cytoscape
        <tessif.visualize.dcgrph>` or ``'nx'`` for
        :mod:`networkx-matplotlib <tessif.visualize.nxgrph>`.

    cls: tuple, default=None
        2-Tuple / :attr:`CLS namedtuple <tessif.frused.namedtuples.CLS>`
        defining the relative flow cost thresholds and the respective style
        specifications. Used to map specific flow costs to edge line style
        representations.

        If  ``None``, default implementation is used based on
        :paramref:`~EdgeFormatier.drawutil`.

        For ``drawutil='nx'``
        `Networkx-Matplotlib
        <https://matplotlib.org/stable/api/_as_gen/matplotlib.patches.Patch.html#matplotlib.patches.Patch.set_linestyle>`_::

            cls = ([0, .33, .66], ['dotted', 'dashed', 'solid'])

        For ``drawutil='dc'``
        `Dash-Cytoscape <https://js.cytoscape.org/#style/edge-line>`_ styles
        are used::

            cls = ([0, .33, .66], ['dotted', 'dashed', 'solid'])

        Translating to all edges of relative specific flows costs, between
        ``0`` and ``.33`` are correlated to have a ``':'``/``'dotted'``
        linestyle.

    See also
    --------
    For functionality documentation see the respective :class:`base class
    <tessif.transform.es2mapping.base.EdgeFormatier>`.
    """

    def __init__(self, optimized_es, drawutil="nx", cls=None, **kwargs):

        super().__init__(
            optimized_es=optimized_es, drawutil=drawutil, cls=cls, **kwargs
        )


class AllFormatier(LabelFormatier, NodeFormatier, MplLegendFormatier, EdgeFormatier):
    r"""
    Transforming ES results into visual expression dicts keyed by attribute.
    Incorporates all the functionalities from its
    parents.

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    cgrp: str, default='name'
        Which group of color attribute(s) to return. One of::

            {'name', 'carrier', 'sector'}

        Color related attributes are grouped by
        :class:`tessif.frused.namedtuples.NodeColorGroupings` and are thus
        returned as a :class:`typing.NamedTuple`. Certain api functionalities
        expect those attributes to be dicts. (Usually those working only
        on :class:`~tessif.transform.es2mapping.base.ESTransformer` input).
        Use this parameter on Formatier creation to provide the expected
        dictionary.

        Used by :class:`NodeFormatier` and :class:`MplLegendFormatier`

    markers: str, default='formatier'
        What marker to use for legend entries. Either ``'formatier'`` or
        one of the :any:`matplotlib.markers`.

        If ``'formatier'`` is used, markers will be inferred from
        :attr:`NodeFormatier.node_shape`.

        Used by :class:`MplLegendFormatier`

    drawutil: str, default='nx'
        Which drawuing utility backend to format node size, fil_size and
        shape to. ``'dc'`` for :mod:`plotly-dash-cytoscape
        <tessif.visualize.dcgrph>` or ``'nx'`` for
        :mod:`networkx-matplotlib <tessif.visualize.nxgrph>`.

    cls: tuple, default=None
        2-Tuple / :attr:`CLS namedtuple <tessif.frused.namedtuples.CLS>`
        defining the relative flow cost thresholds and the respective style
        specifications. Used to map specific flow costs to edge line style
        representations.

        If  ``None``, default implementation is used based on
        :paramref:`~EdgeFormatier.drawutil`.

        For ``drawutil='nx'``
        `Networkx-Matplotlib
        <https://matplotlib.org/stable/api/_as_gen/matplotlib.patches.Patch.html#matplotlib.patches.Patch.set_linestyle>`_::

            cls = ([0, .33, .66], ['dotted', 'dashed', 'solid'])

        For ``drawutil='dc'``
        `Dash-Cytoscape <https://js.cytoscape.org/#style/edge-line>`_ styles
        are used::

            cls = ([0, .33, .66], ['dotted', 'dashed', 'solid'])

        Translating to all edges of relative specific flows costs, between
        ``0`` and ``.33`` are correlated to have a ``':'``/``'dotted'``
        linestyle.

    Note
    ----
    This class allows interfacing with **ALL** framework processing utilities.
    It extracts every bit of info the author ever needed in his postprocessing.

    It is meant to be a "one fits all" solution for small energy systems.
    Perfectly fit for showing "proof of concepts" or debugging energy system
    components.

    **Potentially Unfit For Large System Models**.
    """

    def __init__(
        self,
        optimized_es,
        cgrp="all",
        markers="formatier",
        drawutil="nx",
        cls=None,
        **kwargs
    ):

        super().__init__(
            optimized_es=optimized_es,
            cgrp=cgrp,
            markers=markers,
            drawutil=drawutil,
            **kwargs
        )

        # initializing edge formatier seperately, because of differing
        # init signature causing a wierd unrespecting of drawutl
        super(EdgeFormatier, self).__init__(
            optimized_es=optimized_es, drawutil=drawutil, cls=cls, **kwargs
        )


class ICRHybridier(OmfResultier, base.ICRHybridier):
    """
    Aggregate numerical and visual information for visualizing
    the :ref:`Integrated_Component_Results` (ICR).

    Parameters
    ----------
    optimized_es: :class:`~oemof.core.energy_system.EnergySystem`
        An optimized oemof energy system containing its
        :ref:`results <omf_results>`.

    See also
    --------
    For non :ref:`model <SupportedModels>` specific attributes see
    the respective :class:`base class
    <tessif.transform.es2mapping.base.ICRHybridier>`.
    """

    def __init__(self, optimized_es, colored_by="name", **kwargs):
        base.ICRHybridier.__init__(
            self,
            optimized_es=optimized_es,
            node_formatier=NodeFormatier(optimized_es, cgrp=colored_by),
            edge_formatier=EdgeFormatier(optimized_es),
            mpl_legend_formatier=MplLegendFormatier(optimized_es),
            **kwargs
        )

    @property
    def node_characteristic_value(self):
        r"""Map node label to characteristic value.

        Components of variable size have a characteristic value of ``None``.

        Characteristic value in this context means:

            - :math:`cv = \frac{\text{sum}\left(\text{characteristic flow}
              \right)}{\text{installed capacity}}` for:

                - :class:`~oemof.solph.network.Source` objects
                - :class:`~oemof.solph.network.Sink` objects
                - :class:`~oemof.solph.network.Transformer` objects
                - :class:`~oemof.solph.components.ExtractionTurbineCHP` objects
                - :class:`~oemof.solph.components.GenericCHP` objects
                - :class:`~oemof.solph.components.OffsetTransformer` objects

            - :math:`cv = \frac{\text{sum}\left(\text{SOC}\right)}
              {\text{capacity}}` for:

                - :class:`~oemof.solph.components.GenericStorage`


        Characteristic flow in this context means:

            - :math:`\text{sum}\left(\text{all flows}\right)` for:

                - :class:`~oemof.solph.network.Source` objects
                - :class:`~oemof.solph.network.Sink` objects

            - :math:`\text{sum}\left(\text{outflow}\right)` for:

                - :class:`~oemof.solph.components.OffsetTransformer` objects

            - :math:`\text{sum}\left(0\text{th outflow}\right)` for:

                - :class:`~oemof.solph.network.Transformer` objects
                - :class:`~oemof.solph.components.ExtractionTurbineCHP` objects
                - :class:`~oemof.solph.custom.Link` objects

            - :math:`\text{sum}\left(\text{power outflow}\right)` for:

                - :class:`~oemof.solph.components.GenericCHP` objects

        The **node fillsize** of :ref:`integrated component results graphs
        <Integrated_Component_Results>` scales with the
        **characteristic value**.
        If no capacity is defined (i.e for nodes of variable size, like busses
        or excess sources and sinks, node size is set to it's default (
        :attr:`nxgrph_visualize_defaults[node_fill_size]
        <tessif.frused.defaults.nxgrph_visualize_defaults>`).
        """
        return self._caps.node_characteristic_value
