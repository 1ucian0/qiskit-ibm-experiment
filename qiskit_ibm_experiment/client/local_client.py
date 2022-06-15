# This code is part of Qiskit.
#
# (C) Copyright IBM 2022.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Client for local Quantum experiment services."""

import logging
import os
import uuid
import json
from typing import List, Dict, Optional, Union, Any
import pandas as pd
import numpy as np

from qiskit_ibm_experiment.exceptions import (
    IBMExperimentEntryNotFound,
    IBMExperimentEntryExists,
    RequestsApiError,
)

logger = logging.getLogger(__name__)


class LocalExperimentClient:
    """Client for locally performing database services."""

    experiment_db_columns = [
        "type",
        "device_name",
        "extra",
        "uuid",
        "parent_experiment_uuid",
        "hub_id",
        "group_id",
        "project_id",
        "experiment_id",
        "visibility",
        "tags",
        "jobs",
        "notes",
        "start_time",
        "end_time",
        "updated_at",
    ]
    results_db_columns = [
        "experiment_uuid",
        "device_components",
        "fit",
        "type",
        "tags",
        "quality",
        "verified",
        "uuid",
        "chisq",
        "device_name",
        "created_at",
        "updated_at",
    ]

    def __init__(self, main_dir: str = None, local_save: bool = None) -> None:
        """ExperimentClient constructor.

        Args:
            access_token: The session's access token
            url: The session's base url
            additional_params: additional session parameters
        """
        self._experiments = None
        self._results = None
        self._figures = None
        self._local_save = False
        if local_save and main_dir is not None:
            self._local_save = True
            self.set_paths(main_dir)
            self.create_directories()
        self.init_db()

    def set_paths(self, main_dir):
        """Creates the path to db files and directories"""
        self.main_dir = main_dir
        self.figures_dir = os.path.join(self.main_dir, "figures")
        self.experiments_file = os.path.join(self.main_dir, "experiments.json")
        self.results_file = os.path.join(self.main_dir, "results.json")

    def create_directories(self):
        """Creates the directories needed for the DB if they do not exist"""
        dirs_to_create = [self.main_dir, self.figures_dir]
        for dir_to_create in dirs_to_create:
            if not os.path.exists(dir_to_create):
                os.makedirs(dir_to_create)

    def save(self):
        """Saves the db to disk"""
        if self._local_save:
            self._experiments.to_json(self.experiments_file)
            self._results.to_json(self.results_file)
            self._save_figures()

    def _save_figures(self):
        """Saves the figures to disk"""
        for exp_id in self._figures:
            for figure_name, figure_data in self._figures[exp_id].items():
                filename = f"{exp_id}_{figure_name}"
                with open(os.path.join(self.figures_dir, filename), "wb") as file:
                    file.write(figure_data)

    def serialize(self, df):
        """Serializes db values as JSON"""
        result = df.replace({np.nan: None}).to_dict("records")[0]
        return json.dumps(result)

    def init_db(self):
        """Initializes the db"""
        if self._local_save:
            if os.path.exists(self.experiments_file):
                self._experiments = pd.read_json(self.experiments_file)
            else:
                self._experiments = pd.DataFrame(columns=self.experiment_db_columns)

            if os.path.exists(self.results_file):
                self._results = pd.read_json(self.results_file)
            else:
                self._results = pd.DataFrame(columns=self.results_db_columns)

            if os.path.exists(self.figures_dir):
                self._figures = self._get_figure_list()
            else:
                self._figures = {}
        else:
            self._experiments = pd.DataFrame(columns=self.experiment_db_columns)
            self._results = pd.DataFrame(columns=self.results_db_columns)
            self._figures = {}

        self.save()

    def _get_figure_list(self):
        """Generates the figure dictionary based on stored data on disk"""
        figures = {}
        for exp_id in self._experiments.uuid:
            # exp_id should be str to begin with, so just in case
            exp_id_string = str(exp_id)
            figures_for_exp = {}
            for filename in os.listdir(self.figures_dir):
                if filename.startswith(exp_id_string):
                    with open(os.path.join(self.figures_dir, filename), "rb") as file:
                        figure_data = file.read()
                    figure_name = filename[len(exp_id_string) + 1 :]
                    figures_for_exp[figure_name] = figure_data
            figures[exp_id] = figures_for_exp
        return figures

    def devices(self) -> Dict:
        """Return the device list from the experiment DB."""
        pass

    def experiments(
        self,
        limit: Optional[int] = 10,
        device_components: Optional[Union[str, "DeviceComponent"]] = None,
        experiment_type: Optional[str] = None,
        backend_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        tags_operator: Optional[str] = "OR",
        **filters: Any,
    ) -> str:
        """Retrieve experiments, with optional filtering.

        Args:
            limit: Number of experiments to retrieve.
            marker: Marker used to indicate where to start the next query.
            backend_name: Name of the backend.
            experiment_type: Experiment type.
            start_time: A list of timestamps used to filter by experiment start time.
            device_components: A list of device components used for filtering.
            tags: Tags used for filtering.
            hub: Filter by hub.
            group: Filter by hub and group.
            project: Filter by hub, group, and project.
            exclude_public: Whether or not to exclude experiments with a public share level.
            public_only: Whether or not to only return experiments with a public share level.
            exclude_mine: Whether or not to exclude experiments where I am the owner.
            mine_only: Whether or not to only return experiments where I am the owner.
            parent_id: Filter by parent experiment ID.
            sort_by: Sorting order.

        Returns:
            A list of experiments and the marker, if applicable.

        Raises:
            ValueError: If the parameters are unsuitable for filtering
        """
        df = self._experiments

        if experiment_type is not None:
            if experiment_type[:5] == "like:":
                experiment_type = experiment_type.split(":")[1]
                df = df.loc[df.type.str.contains(experiment_type)]
            else:
                df = df.loc[df.type == experiment_type]

        if backend_name is not None:
            df = df.loc[df.device_name == backend_name]

        # Note a bug in the interface for all services:
        # It is impossible to filter by experiments whose parent id is None
        # (i.e., root experiments)
        if parent_id is not None:
            df = df.loc[df.parent_experiment_uuid == parent_id]

        # Waiting for consistency between provider service and qiskit-experiments service,
        # currently they have different types for `device_components`
        if device_components is not None:
            raise ValueError(
                "The fake service currently does not support filtering on device components"
            )

        if tags is not None:
            if tags_operator == "OR":
                df = df.loc[
                    df.tags.apply(lambda dftags: any(x in dftags for x in tags))
                ]
            elif tags_operator == "AND":
                df = df.loc[
                    df.tags.apply(lambda dftags: all(x in dftags for x in tags))
                ]
            else:
                raise ValueError("Unrecognized tags operator")

        if "start_datetime_before" in filters:
            df = df.loc[df.start_time <= filters["start_datetime_before"]]
        if "start_datetime_after" in filters:
            df = df.loc[df.start_time >= filters["start_datetime_after"]]

        sort_by = filters.get("sort_by")
        if sort_by is None:
            sort_by = "start_datetime:desc"

        if not isinstance(sort_by, list):
            sort_by = [sort_by]

        # TODO: support also experiment_type
        if len(sort_by) != 1:
            raise ValueError(
                "The fake service currently supports only sorting by start_datetime"
            )

        sortby_split = sort_by[0].split(":")
        # TODO: support also experiment_type
        if (
            len(sortby_split) != 2
            or sortby_split[0] != "start_datetime"
            or (sortby_split[1] != "asc" and sortby_split[1] != "desc")
        ):
            raise ValueError(
                "The fake service currently supports only sorting by start_datetime, which can be "
                "either asc or desc"
            )

        df = df.sort_values(
            ["start_time", "uuid"], ascending=[(sortby_split[1] == "asc"), True]
        )

        df = df.iloc[:limit]
        result = {"experiments": df.replace({np.nan: None}).to_dict("records")}
        return json.dumps(result)

    def experiment_get(self, experiment_id: str) -> str:
        """Get a specific experiment.

        Args:
            experiment_id: Experiment uuid.

        Returns:
            Experiment data.

        Raises:
            IBMExperimentEntryNotFound: If the experiment is not found
        """
        exp = self._experiments.loc[self._experiments.uuid == experiment_id]
        if exp.empty:
            raise IBMExperimentEntryNotFound
        return self.serialize(exp)

    def experiment_upload(self, data: str) -> Dict:
        """Upload an experiment.

        Args:
            data: Experiment data.

        Returns:
            Experiment data.

        Raises:
            IBMExperimentEntryExists: If the experiment already exists

        """
        data_dict = json.loads(data)
        if "uuid" not in data_dict:
            data_dict["uuid"] = str(uuid.uuid4())
        exp = self._experiments.loc[self._experiments.uuid == data_dict["uuid"]]
        if not exp.empty:
            raise IBMExperimentEntryExists

        new_df = pd.DataFrame([data_dict], columns=self._experiments.columns)
        self._experiments = pd.concat([self._experiments, new_df], ignore_index=True)
        self.save()
        return data_dict

    def experiment_update(self, experiment_id: str, new_data: str) -> Dict:
        """Update an experiment.

        Args:
            experiment_id: Experiment UUID.
            new_data: New experiment data.

        Returns:
            Experiment data.

        Raises:
            IBMExperimentEntryNotFound: If the experiment is not found
        """
        exp = self._experiments.loc[self._experiments.uuid == experiment_id]
        if exp.empty:
            raise IBMExperimentEntryNotFound
        exp_index = exp.index[0]
        new_data_dict = json.loads(new_data)
        for key, value in new_data_dict.items():
            self._experiments.at[exp_index, key] = value
        self.save()
        exp = self._experiments.loc[self._experiments.uuid == experiment_id]
        return self.serialize(exp)

    def experiment_delete(self, experiment_id: str) -> Dict:
        """Delete an experiment.

        Args:
            experiment_id: Experiment UUID.

        Returns:
            JSON response.

        Raises:
            IBMExperimentEntryNotFound: If the experiment is not found
        """
        exp = self._experiments.loc[self._experiments.uuid == experiment_id]
        if exp.empty:
            raise IBMExperimentEntryNotFound
        self._experiments.drop(
            self._experiments.loc[self._experiments.uuid == experiment_id].index,
            inplace=True,
        )
        self.save()
        return self.serialize(exp)

    def experiment_plot_upload(
        self,
        experiment_id: str,
        plot: Union[bytes, str],
        plot_name: str,
    ) -> bool:
        """Upload an experiment plot.

        Args:
            experiment_id: Experiment UUID.
            plot: Plot file name or data to upload.
            plot_name: Name of the plot.

        Returns:
            Whether the upload succeeded

        Raises:
            RequestsApiError: If the figure already exists
        """
        if experiment_id not in self._figures:
            self._figures[experiment_id] = {}
        exp_figures = self._figures[experiment_id]
        if plot_name in exp_figures:
            raise RequestsApiError(
                f"Figure {plot_name} already exists", status_code=409
            )
        exp_figures[plot_name] = plot
        self.save()
        return True

    def experiment_plot_update(
        self,
        experiment_id: str,
        plot: Union[bytes, str],
        plot_name: str,
    ) -> Dict:
        """Update an experiment plot.

        Args:
            experiment_id: Experiment UUID.
            plot: Plot file name or data to upload.
            plot_name: Name of the plot.

        Returns:
            JSON response.

        Raises:
            RequestsApiError: If the figure is not found
        """
        exp_figures = self._figures[experiment_id]
        if plot_name not in exp_figures:
            raise RequestsApiError(f"Figure {plot_name} not found", status_code=404)
        exp_figures[plot_name] = plot
        self.save()
        return json.dumps({"name": plot_name, "size": len(plot)})

    def experiment_plot_get(self, experiment_id: str, plot_name: str) -> bytes:
        """Retrieve an experiment plot.

        Args:
            experiment_id: Experiment UUID.
            plot_name: Name of the plot.

        Returns:
            Retrieved experiment plot.

        Raises:
            RequestsApiError: If the figure is not found
        """

        exp_figures = self._figures[experiment_id]
        if plot_name not in exp_figures:
            raise RequestsApiError(f"Figure {plot_name} not found", status_code=404)
        return exp_figures[plot_name]

    def experiment_plot_delete(self, experiment_id: str, plot_name: str) -> None:
        """Delete an experiment plot.

        Args:
            experiment_id: Experiment UUID.
            plot_file_name: Plot file name.

        Raises:
            RequestsApiError: If the figure is not found
        """
        exp_figures = self._figures[experiment_id]
        if plot_name not in exp_figures:
            raise RequestsApiError(f"Figure {plot_name} not found", status_code=404)
        del exp_figures[plot_name]

    def experiment_devices(self) -> List:
        """Return list of experiment devices.

        Returns:
            A list of experiment devices.
        """
        pass

    def analysis_results(
        self,
        limit: Optional[int],
        marker: Optional[str],
        backend_name: Optional[str] = None,
        device_components: Optional[List[str]] = None,
        experiment_uuid: Optional[str] = None,
        result_type: Optional[str] = None,
        quality: Optional[Union[str, List[str]]] = None,
        verified: Optional[bool] = None,
        tags: Optional[List[str]] = None,
        created_at: Optional[List] = None,
        sort_by: Optional[str] = None,
    ) -> str:
        """Return a list of analysis results.

        Args:
            limit: Number of analysis results to retrieve.
            marker: Marker used to indicate where to start the next query.
            backend_name: Name of the backend.
            device_components: A list of device components used for filtering.
            experiment_uuid: Experiment UUID used for filtering.
            result_type: Analysis result type used for filtering.
            quality: Quality value used for filtering.
            verified: Indicates whether this result has been verified.
            tags: Filter by tags assigned to analysis results.
            created_at: A list of timestamps used to filter by creation time.
            sort_by: Indicates how the output should be sorted.

        Returns:
            A list of analysis results and the marker, if applicable.
        Raises:
            ValueError: If the parameters are unsuitable for filtering
        """
        # pylint: disable=unused-argument
        df = self._results

        # TODO: skipping device components for now until we conslidate more with the provider service
        # (in the qiskit-experiments service there is no operator for device components,
        # so the specification for filtering is not clearly defined)

        if experiment_uuid is not None:
            df = df.loc[df.experiment_uuid == experiment_uuid]
        if result_type is not None:
            if result_type[:5] == "like:":
                result_type = result_type.split(":")[1]
                df = df.loc[df.type.str.contains(result_type)]
            else:
                df = df.loc[df.type == result_type]
        if backend_name is not None:
            df = df.loc[df.backend_name == backend_name]
        if quality is not None:
            df = df.loc[df.quality == quality]
        if verified is not None:
            df = df.loc[df.verified == verified]

        if tags is not None:
            operator, tags = tags.split(":")
            tags = tags.split(",")
            if operator == "any:":  # OR operator
                df = df.loc[
                    df.tags.apply(lambda dftags: any(x in dftags for x in tags))
                ]
            elif operator == "AND":
                df = df.loc[
                    df.tags.apply(lambda dftags: all(x in dftags for x in tags))
                ]
            else:
                raise ValueError(f"Unrecognized tags operator {operator}")

        if sort_by is None:
            sort_by = "creation_datetime:desc"

        if not isinstance(sort_by, list):
            sort_by = [sort_by]

        # TODO: support also device components and result type
        if len(sort_by) != 1:
            raise ValueError(
                "The fake service currently supports only sorting by creation_datetime"
            )

        sortby_split = sort_by[0].split(":")
        # TODO: support also device components and result type
        if (
            len(sortby_split) != 2
            or sortby_split[0] != "creation_datetime"
            or (sortby_split[1] != "asc" and sortby_split[1] != "desc")
        ):
            raise ValueError(
                "The fake service currently supports only sorting by creation_datetime, "
                "which can be either asc or desc"
            )

        df = df.sort_values(
            ["created_at", "uuid"], ascending=[(sortby_split[1] == "asc"), True]
        )

        df = df.iloc[:limit]
        result = {"analysis_results": df.replace({np.nan: None}).to_dict("records")}
        return json.dumps(result)

    def analysis_result_create(self, result: str) -> Dict:
        """Upload an analysis result.

        Args:
            result: The analysis result to upload.

        Returns:
            Analysis result data.

        Raises:
            RequestsApiError: If experiment id is missing
        """
        data_dict = json.loads(result)
        exp_id = data_dict.get("experiment_uuid")
        if exp_id is None:
            raise RequestsApiError(
                f"Cannot create analysis result without experiment id"
            )
        exp = self._experiments.loc[self._experiments.uuid == exp_id]
        if exp.empty:
            raise RequestsApiError(f"Experiment {exp_id} not found", status_code=404)
        exp_index = exp.index[0]
        data_dict["device_name"] = self._experiments.at[exp_index, "device_name"]
        if "uuid" not in data_dict:
            data_dict["uuid"] = str(uuid.uuid4())

        new_df = pd.DataFrame([data_dict], columns=self._results.columns)
        self._results = pd.concat([self._results, new_df], ignore_index=True)
        self.save()
        return data_dict

    def analysis_result_update(self, result_id: str, new_data: str) -> Dict:
        """Update an analysis result.

        Args:
            result_id: Analysis result ID.
            new_data: New analysis result data.

        Returns:
            Analysis result data.

        Raises:
            IBMExperimentEntryNotFound: If the analysis result is not found
        """
        result = self._results.loc[self._results.uuid == result_id]
        if result.empty:
            raise IBMExperimentEntryNotFound
        result_index = result.index[0]
        new_data_dict = json.loads(new_data)
        for key, value in new_data_dict.items():
            self._results.at[result_index, key] = value
        self.save()
        result = self._results.loc[self._results.uuid == result_id]
        return self.serialize(result)

    def analysis_result_delete(self, result_id: str) -> Dict:
        """Delete an analysis result.

        Args:
            result_id: Analysis result ID.

        Returns:
            Analysis result data.

        Raises:
            IBMExperimentEntryNotFound: If the analysis result is not found
        """
        result = self._results.loc[self._results.uuid == result_id]
        if result.empty:
            raise IBMExperimentEntryNotFound
        self._results.drop(
            self._results.loc[self._results.uuid == result_id].index, inplace=True
        )
        self.save()
        return self.serialize(result)

    def analysis_result_get(self, result_id: str) -> str:
        """Retrieve an analysis result.

        Args:
            result_id: Analysis result ID.

        Returns:
            Analysis result data.
        Raises:
            IBMExperimentEntryNotFound: If the analysis result is not found
        """
        result = self._results.loc[self._results.uuid == result_id]
        if result.empty:
            raise IBMExperimentEntryNotFound
        return self.serialize(result)

    def device_components(self, backend_name: Optional[str]) -> List[Dict]:
        """Return device components for the backend.

        Args:
            backend_name: Name of the backend.

        Returns:
            A list of device components.
        """
        pass