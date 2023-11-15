# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Implementation of energy dependent estimator tool."""
import numpy as np
from astropy.table import Table
from gammapy.datasets import Datasets
from gammapy.modeling import Fit
from gammapy.modeling.models import FoVBackgroundModel
from gammapy.modeling.selection import TestStatisticNested
from gammapy.stats.utils import ts_to_sigma
from .core import Estimator

__all__ = ["EnergyDependenceEstimator"]


def weighted_chi2_parameter(results_edep, parameter="sigma"):
    """Calculate the weighted chi-squared value for the parameter of interest.

    Parameters
    ----------
    result_edep : dict
        Dictionary of results for the energy-dependent estimator.
    parameter : str, optional
        The model parameter to calculate the chi-squared value for.
        Default is "sigma".

    Returns
    -------
    chi2_result : dict
        Dictionary with the chi-squared value for parameter of interest.
    """

    table_edep = Table(results_edep)

    values = table_edep[parameter][1:]
    errors = table_edep[f"{parameter}_err"][1:]

    weights = 1 / errors**2
    avg = np.average(values, weights=weights)

    chi2_value = np.sum((values - avg) ** 2 / errors**2)
    df = len(values) - 1
    sigma_value = ts_to_sigma(chi2_value, df)

    chi2_result = {}
    chi2_result[f"chi2 {parameter}"] = [chi2_value]
    chi2_result["df"] = [df]
    chi2_result["significance"] = [sigma_value]

    return chi2_result


class EnergyDependenceEstimator(Estimator):
    """Test if there is any energy-dependent morphology in a map dataset for a given set of energy bins.

    Parameters
    ----------
    energy_edges : `~astropy.units.Quantity`
        Energy edges for the energy-dependence test.
    source : str or int
        For which source in the model to compute the estimator.
    fit : `~gammapy.modeling.Fit`, optional
        Fit instance specifying the backend and fit options.
        If None, the fit backend default is minuit.
        Default is None.

    """

    tag = "EnergyDependenceEstimator"

    def __init__(self, energy_edges, source, fit=None):

        self.energy_edges = energy_edges
        self.source = source
        self.num_energy_bands = len(self.energy_edges) - 1

        if fit is None:
            fit = Fit(optimize_opts={"print_level": 1})

        self.fit = fit

    def estimate_source_significance(self, datasets):
        """Estimate the significance of the source above the background.

        Parameters
        ----------
        datasets : `~gammapy.datasets.Datasets`
            Input datasets to use.

        Returns
        -------
        result_bkg_src : dict
            Dictionary with the results of the null hypothesis with no source, and alternative
            hypothesis with the source added in. Entries are:
            * "Emin" : the minimum energy of the energy band
            * "Emax" : the maximum energy of the energy band
            * "delta_ts" : difference in ts
            * "df" : the degrees of freedom between null and alternative hypothesis
            * "significance" : significance of the result
        """
        for dataset in datasets:
            dataset.mask_fit = dataset.counts.geom.energy_mask(
                energy_min=self.energy_edges[0], energy_max=None
            )

        model = datasets.models[self.source]

        # Calculate the dataset for each energy slice
        slices_src = Datasets()
        for emin, emax in zip(self.energy_edges[:-1], self.energy_edges[1:]):
            for dataset in datasets:
                sliced_src = dataset.slice_by_energy(emin, emax)
                bkg_sliced_model = FoVBackgroundModel(dataset_name=sliced_src.name)
                sliced_src.models = [model.copy(), bkg_sliced_model]
                slices_src.append(sliced_src)

        # Norm is free and fit
        test_results = []
        for sliced in slices_src:
            parameters = [param for param in sliced.models.parameters.free_parameters]
            null_values = [0] + [
                param.value
                for param in sliced.models[0].spatial_model.parameters.free_parameters
            ]

            test = TestStatisticNested(
                parameters=parameters,
                null_values=null_values,
                n_sigma=-np.inf,
                fit=self.fit,
            )
            test_results.append(test.run(sliced))

        delta_ts_bkg_src = [_["ts"] for _ in test_results]
        df_src = [
            len(_["fit_results"].parameters.free_parameters.names) for _ in test_results
        ]
        df_bkg = 1
        df_bkg_src = df_src[0] - df_bkg
        sigma_ts_bkg_src = ts_to_sigma(delta_ts_bkg_src, df=df_bkg_src)

        # Prepare results dictionary for signal above background
        result_bkg_src = {}

        result_bkg_src["Emin"] = self.energy_edges[:-1]
        result_bkg_src["Emax"] = self.energy_edges[1:]
        result_bkg_src["delta_ts"] = delta_ts_bkg_src
        result_bkg_src["df"] = [df_bkg_src] * self.num_energy_bands
        result_bkg_src["significance"] = [elem for elem in sigma_ts_bkg_src]

        return result_bkg_src

    def estimate_energy_dependence(self, datasets):
        """Estimate the potential of energy-dependent morphology.

        Parameters
        ----------
        datasets : `~gammapy.datasets.Datasets`
            Input dataset to use.

        Returns
        -------
        results : `dict`
            Dictionary with results of the energy-dependence test. Entries are:
            * "delta_ts" : difference in ts between fitting each energy band individually (sliced fit) and the joint fit
            * "df" : the degrees of freedom between fitting each energy band individually (sliced fit) and the joint fit
            * "result" : the results for the fitting each energy band individually (sliced fit) and the joint fit
        """
        for dataset in datasets:
            dataset.mask_fit = dataset.counts.geom.energy_mask(
                energy_min=self.energy_edges[0], energy_max=None
            )

        model = datasets.models[self.source]

        # Calculate the individually sliced components
        slices_src = Datasets()
        for emin, emax in zip(self.energy_edges[:-1], self.energy_edges[1:]):
            for dataset in datasets:
                sliced_src = dataset.slice_by_energy(emin, emax)
                bkg_sliced_model = FoVBackgroundModel(dataset_name=sliced_src.name)
                sliced_src.models = [model.copy(), bkg_sliced_model]
                slices_src.append(sliced_src)

        results_src = []
        for sliced in slices_src:
            results_src.append(self.fit.run(sliced))

        results_src_total_stat = [result.total_stat for result in results_src]
        free_x, free_y = np.shape(
            [result.parameters.free_parameters.names for result in results_src]
        )
        df_src = free_x * free_y

        # Calculate the joint fit
        parameters = model.spatial_model.parameters.free_parameters.names
        slice0 = slices_src[0]
        for slice_j in slices_src[1:]:
            for param in parameters:
                setattr(
                    slice_j.models[0].spatial_model,
                    param,
                    slice0.models[0].spatial_model.parameters[param],
                )
        result_joint = self.fit.run(slices_src)

        # Compare fit of individual energy slices to the results with joint fit
        delta_ts_joint = result_joint.total_stat - np.sum(results_src_total_stat)
        df_joint = len(slices_src.parameters.free_parameters.names)
        df = df_src - df_joint

        # Prepare results dictionary
        joint_values = [result_joint.parameters[param].value for param in parameters]
        joint_errors = [result_joint.parameters[param].error for param in parameters]

        parameter_values = np.empty((len(parameters), self.num_energy_bands))
        parameter_errors = np.empty((len(parameters), self.num_energy_bands))
        for i in range(self.num_energy_bands):
            parameter_values[:, i] = [
                results_src[i].parameters[param].value for param in parameters
            ]
            parameter_errors[:, i] = [
                results_src[i].parameters[param].error for param in parameters
            ]

        result = {}

        result["Hypothesis"] = ["H0"] + ["H1"] * self.num_energy_bands

        result["Emin"] = np.append(self.energy_edges[0], self.energy_edges[:-1])
        result["Emax"] = np.append(self.energy_edges[-1], self.energy_edges[1:])

        units = [result_joint.parameters[param].unit for param in parameters]

        # Results for H0 in the first row and then H1 -- i.e. individual bands in other rows
        for i in range(len(parameters)):
            result[f"{parameters[i]}"] = np.append(
                joint_values[i] * units[i], parameter_values[i] * units[i]
            )
            result[f"{parameters[i]}_err"] = np.append(
                joint_errors[i] * units[i], parameter_errors[i] * units[i]
            )

        return dict(delta_ts=delta_ts_joint, df=df, result=result)

    def run(self, dataset):
        """Run the energy-dependence estimator.

        Parameters
        ----------
        dataset : `~gammapy.datasets.MapDataset`
            Input dataset to use.

        Returns
        -------
        results : dict
            Dictionary with the various energy-dependence estimation values.
        """

        if not isinstance(dataset, Datasets):
            raise ValueError("Unsupported dataset type.")

        results = self.estimate_energy_dependence(dataset)
        results = dict(
            energy_dependence=results,
            src_above_bkg=self.estimate_source_significance(dataset),
        )

        return results
