from __future__ import annotations
import numpy as np
import cvxpy as cp
from typing import List, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from portpy.photon.plan import Plan
    from portpy.photon.influence_matrix import InfluenceMatrix
from .clinical_criteria import ClinicalCriteria


class Optimization(object):
    """
    Optimization class for optimizing and creating the plan

    :param my_plan: object of class Plan
    :param inf_matrix: object of class InfluenceMatrix
    :param clinical_criteria: clinical criteria for which plan to be optimized
    :param opt_params: optimization parameters for modifying parameters of problem statement

    - **Attributes** ::

        :param obj: List containing individual objective function
        :param constraints: List containing individual constraints
        :param vars: Dictionary containing variable
        :Example
                dict = {"x": [...]}

    - **Methods** ::
        :create_cvxpy_problem(my_plan)
            Create cvxpy objective function and constraints and save them as a list

    """

    def __init__(self, my_plan: Plan, inf_matrix: InfluenceMatrix = None,
                 clinical_criteria: ClinicalCriteria = None,
                 opt_params: dict = None):
        # self.x = None
        self.my_plan = my_plan
        if inf_matrix is None:
            inf_matrix = my_plan.inf_matrix
        self.inf_matrix = inf_matrix
        if clinical_criteria is None:
            clinical_criteria = my_plan.clinical_criteria
        self.clinical_criteria = clinical_criteria
        self.opt_params = opt_params
        self.prescription_gy = opt_params['prescription_gy']
        self.obj = []
        self.constraints = []
        self.vars = {}

    def create_cvxpy_problem(self):
        """
        It runs optimization to create optimal plan based upon clinical criteria

        :return: cvxpy problem object

        """

        # unpack data
        my_plan = self.my_plan
        inf_matrix = self.inf_matrix
        opt_params = self.opt_params
        clinical_criteria = self.clinical_criteria
        # self.prescription_gy = opt_params['prescription_gy']

        # get opt params for optimization
        obj_funcs = opt_params['objective_functions'] if 'objective_functions' in opt_params else []
        opt_params_constraints = opt_params['constraints'] if 'constraints' in opt_params else []

        # Creating rinds (aka rings, shells)
        # Rinds are doughnut-shaped structures often created to control the radiation dose to non-specified structures
        #   They can also control the dose fall-off after PTV.

        A = inf_matrix.A
        num_fractions = clinical_criteria.get_num_of_fractions()
        st = inf_matrix

        # Construct optimization problem
        obj = []
        constraints = []
        x = cp.Variable(A.shape[1], pos=True, name='x')

        self.vars = {'x': x}

        # Generating objective functions
        print('Objective Start')
        for i in range(len(obj_funcs)):
            if obj_funcs[i]['type'] == 'quadratic-overdose':
                if obj_funcs[i]['structure_name'] in my_plan.structures.get_structures():
                    struct = obj_funcs[i]['structure_name']
                    dose_gy = self.get_num(obj_funcs[i]['dose_gy']) / clinical_criteria.get_num_of_fractions()
                    dO = cp.Variable(len(st.get_opt_voxels_idx(struct)), pos=True)
                    obj += [(1 / len(st.get_opt_voxels_idx(struct))) *
                            (obj_funcs[i]['weight'] * cp.sum_squares(dO))]
                    constraints += [A[st.get_opt_voxels_idx(struct), :] @ x <= dose_gy + dO]
            elif obj_funcs[i]['type'] == 'quadratic-underdose':
                if obj_funcs[i]['structure_name'] in my_plan.structures.get_structures():
                    struct = obj_funcs[i]['structure_name']
                    dose_gy = self.get_num(obj_funcs[i]['dose_gy']) / clinical_criteria.get_num_of_fractions()
                    dU = cp.Variable(len(st.get_opt_voxels_idx(struct)), pos=True)
                    obj += [(1 / len(st.get_opt_voxels_idx(struct))) *
                            (obj_funcs[i]['weight'] * cp.sum_squares(dU))]
                    constraints += [A[st.get_opt_voxels_idx(struct), :] @ x >= dose_gy - dU]
            elif obj_funcs[i]['type'] == 'quadratic':
                if obj_funcs[i]['structure_name'] in my_plan.structures.get_structures():
                    struct = obj_funcs[i]['structure_name']
                    obj += [(1 / len(st.get_opt_voxels_idx(struct))) * (
                            obj_funcs[i]['weight'] * cp.sum_squares(A[st.get_opt_voxels_idx(struct), :] @ x))]
            elif obj_funcs[i]['type'] == 'smoothness-quadratic':
                [Qx, Qy, num_rows, num_cols] = self.get_smoothness_matrix(inf_matrix.beamlets_dict)
                smoothness_X_weight = 0.6
                smoothness_Y_weight = 0.4
                obj += [obj_funcs[i]['weight'] * (smoothness_X_weight * (1 / num_cols) * cp.sum_squares(Qx @ x) +
                                                  smoothness_Y_weight * (1 / num_rows) * cp.sum_squares(Qy @ x))]

        print('Objective done')

        print('Constraints Start')

        # add optimization constraints if present in opt params
        for param in opt_params_constraints:
            # add constraint
            if param['structure_name'] in my_plan.structures.get_structures():
                parameters = {'structure_name': param['structure_name']}
                # total_pres = self.get_prescription()
                if 'max' in param['type']:
                    opt_constraints = {'limit_dose_gy': self.get_num(param['upper_limit'])}
                    clinical_criteria.add_criterion(criterion='max_dose', parameters=parameters,
                                                    constraints=opt_constraints)
                if 'mean' in param['type']:
                    opt_constraints = {'limit_dose_gy': self.get_num(param['upper_limit'])}
                    clinical_criteria.add_criterion(criterion='mean_dose', parameters=parameters,
                                                    constraints=opt_constraints)

        # get max and mean constraints
        max_constraints = clinical_criteria.get_criteria(
            name='max_dose')  # returns all the max dose criteria as a list
        mean_constraints = clinical_criteria.get_criteria(
            name='mean_dose')  # returns all the mean dose criteria as a list

        # Adding max constraints
        for i in range(len(max_constraints)):
            if 'max_dose' in max_constraints[i]['name']:
                if 'limit_dose_gy' in max_constraints[i]['constraints']:
                    limit = max_constraints[i]['constraints']['limit_dose_gy']
                    org = max_constraints[i]['parameters']['structure_name']
                    if org != 'GTV' and org != 'CTV':
                        if org in my_plan.structures.structures_dict['name']:
                            constraints += [A[st.get_opt_voxels_idx(org), :] @ x <= limit / num_fractions]
                        else:
                            print('Structure {} not available!'.format(org))

        # Adding mean constraints
        for i in range(len(mean_constraints)):
            if 'mean_dose' in mean_constraints[i]['name']:
                if 'limit_dose_gy' in mean_constraints[i]['constraints']:
                    limit = mean_constraints[i]['constraints']['limit_dose_gy']
                    org = mean_constraints[i]['parameters']['structure_name']
                    # mean constraints using voxel weights
                    if org in my_plan.structures.structures_dict['name']:
                        constraints += [(1 / sum(st.get_opt_voxels_volume_cc(org))) *
                                        (cp.sum((cp.multiply(st.get_opt_voxels_volume_cc(org), A[st.get_opt_voxels_idx(org),
                                                                                          :] @ x)))) <= limit / num_fractions]

                    else:
                        print('Structure {} not available!'.format(org))

        self.obj = obj
        self.constraints = constraints

        print('Constraints done')

    def add_max(self, struct: str, dose_gy: float):
        """
        Add max constraints to the problem

        :param struct: struct_name name
        :param dose_gy: dose in Gy per fraction.
        :return:
        """
        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        max_constraint = [A[st.get_opt_voxels_idx(struct), :] @ x <= dose_gy]
        self.add_constraints(max_constraint)

    def add_mean(self, struct: str, dose_gy: float):
        """
        Add mean constraints to the problem

        :param struct: struct_name name
        :param dose_gy: dose in Gy per fraction.
        :return:
        """
        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        mean_constraint = [(1 / sum(st.get_opt_voxels_volume_cc(struct))) *
                           (cp.sum((cp.multiply(st.get_opt_voxels_volume_cc(struct),
                                                A[st.get_opt_voxels_idx(struct),
                                                :] @ x)))) <= dose_gy]
        self.add_constraints(mean_constraint)

    def add_overdose_quad(self, struct: str, dose_gy: float, weight: float = 10000):
        """
        Add quadratic loss for the overdose voxels of the struct_name

        :param struct: struct_name name
        :param dose_gy: dose in Gy per fraction.
        :param weight: penalty/weight in the objective for overdose
        :return:
        """
        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        dO = cp.Variable(len(st.get_opt_voxels_idx(struct)), pos=True, name='{}_overdose'.format(struct))
        obj = (1 / len(st.get_opt_voxels_idx(struct))) * (weight * cp.sum_squares(dO))
        self.add_objective(obj)
        self.add_constraints([A[st.get_opt_voxels_idx('PTV'), :] @ x <= dose_gy + dO])

    def add_underdose_quad(self, struct: str, dose_gy: float, weight: float = 100000):
        """
        Add quadratic loss for the underdose voxels of the struct_name

        :param struct: struct_name name
        :param dose_gy: dose in Gy per fraction.
        :param weight: penalty/weight in the objective for underdose
        :return:
        """
        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        dU = cp.Variable(len(st.get_opt_voxels_idx(struct)), pos=True, name='{}_underdose'.format(struct))
        obj = (1 / len(st.get_opt_voxels_idx(struct))) * (weight * cp.sum_squares(dU))
        self.add_objective(obj)
        self.add_constraints([A[st.get_opt_voxels_idx('PTV'), :] @ x >= dose_gy - dU])

    def add_quad(self, struct: str = None, voxels: np.ndarray = None, weight: float = 10,
                 voxels_weight: np.ndarray = None):

        """
        Add quadratic objective to the optimization problem

        :param struct: struct_name for which quadratic loss is added to objective function
        :param voxels: Default to None. If set, quadratic loss will be added for the given voxels
        :param weight: Default to 10. penalty in the objective function for the given struct_name.
        :param voxels_weight: weight for each voxel in the objective function
        :return:
        """
        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        obj = 0
        if voxels is not None:
            if voxels_weight is None:
                raise Exception('Please input weight array for the input voxels')
            obj = (1 / A[voxels, :].shape[0]) * cp.sum_squares(
                cp.multiply(cp.sqrt(voxels_weight), A[voxels, :] @ x))
        if struct is not None:
            obj = (1 / len(st.get_opt_voxels_idx(struct))) * (
                    weight * cp.sum_squares(A[st.get_opt_voxels_idx(struct), :] @ x))
        self.add_objective(obj)

    def add_smoothness_quad(self, weight: int = 10, smoothness_X_weight: int = 0.6, smoothness_Y_weight: int = 0.4):
        """
        Add quadratic smoothness to the optimization problem

        :param weight: smoothness weight
        :param smoothness_X_weight: weight in X direction of MLC (parallel to MLC)
        :param smoothness_Y_weight: weight in Y direction of MLC (perpendicular to MLC)
        :return:
        """

        st = self.inf_matrix
        x = self.vars['x']

        [Qx, Qy, num_rows, num_cols] = self.get_smoothness_matrix(st.beamlets_dict)
        obj = weight * (
                smoothness_X_weight * (1 / num_cols) * cp.sum_squares(Qx @ x) + smoothness_Y_weight * (1 / num_rows)
                * cp.sum_squares(Qy @ x))
        self.add_objective(obj)

    def add_constraints(self, constraints: list):
        """
        Add constraint to the constraint list of problem

        :param constraints: list of constraints
        :return:
        """
        self.constraints += constraints

    def add_objective(self, obj):
        """
        Add objective function to objective list of the problem

        :param obj: objective function expression using cvxpy
        :return:
        """
        if not isinstance(obj, list):
            obj = [obj]

        self.obj += obj

    def add_boo(self, num_beams: int):
        """
        Select optimal beams from set of beams using MIP

        :param num_beams: number of beams to be selected
        :return:
        """
        st = self.inf_matrix
        x = self.vars['x']

        #  Constraints for selecting beams
        # binary variable for selecting beams
        b = cp.Variable(len(st.beamlets_dict), boolean=True)

        constraints = []
        constraints += [cp.sum(b) <= num_beams]
        for i in range(len(st.beamlets_dict)):
            start_beamlet = st.beamlets_dict[i]['start_beamlet_idx']
            end_beamlet_idx = st.beamlets_dict[i]['end_beamlet_idx']
            M = 50  # upper bound on the beamlet intensity
            constraints += [x[start_beamlet:end_beamlet_idx] <= b[i] * M]

        self.add_constraints(constraints)

    def solve(self, *args, **kwargs) -> dict:
        """
                Return optimal solution and influence matrix associated with it in the form of dictionary

                :Example
                        dict = {"optimal_fluence": [..],
                        "inf_matrix": my_plan.inf_marix
                        }

                :return: solution dictionary
                """

        problem = cp.Problem(cp.Minimize(sum(self.obj)), constraints=self.constraints)
        problem.solve(*args, **kwargs)
        return {'optimal_intensity': self.vars['x'].value, 'inf_matrix': self.inf_matrix}

    def get_sol(self) -> dict:
        """
        Return optimal solution and influence matrix associated with it in the form of dictionary

        :Example
                dict = {"optimal_fluence": [..],
                "inf_matrix": my_plan.inf_marix
                }

        :return: solution dictionary
        """
        return {'optimal_intensity': self.vars['x'].value, 'inf_matrix': self.inf_matrix}

    def add_dvh(self, dvh_constraint: list):

        A = self.inf_matrix.A
        st = self.inf_matrix
        x = self.vars['x']

        import pandas as pd
        df_dvh_criteria = pd.DataFrame()
        count = 0
        criteria = self.clinical_criteria.clinical_criteria_dict['criteria']
        for i in range(len(dvh_constraint)):
            if 'dose_volume' in dvh_constraint[i]['name']:
                limit_key = self.matching_keys(dvh_constraint[i]['constraints'], 'limit')
                if limit_key in dvh_constraint[i]['constraints']:
                    df_dvh_criteria.at[count, 'structure_name'] = dvh_constraint[i]['parameters']['structure_name']
                    df_dvh_criteria.at[count, 'dose_gy'] = dvh_constraint[i]['parameters']['dose_gy']

                    # getting max dose_1d for the same struct_name
                    max_dose_struct = 1000
                    for j in range(len(criteria)):
                        if 'max_dose' in criteria[j]['name']:
                            if 'limit_dose_gy' in criteria[j]['constraints']:
                                org = criteria[j]['parameters']['structure_name']
                                if org == dvh_constraint[i]['parameters']['structure_name']:
                                    max_dose_struct = criteria[j]['constraints']['limit_dose_gy']
                    df_dvh_criteria.at[count, 'M'] = max_dose_struct - dvh_constraint[i]['parameters']['dose_gy']
                    if 'perc' in limit_key:
                        df_dvh_criteria.at[count, 'vol_perc'] = dvh_constraint[i]['constraints'][limit_key]
                    count = count + 1

        # binary variable for dvh constraints
        b_dvh = cp.Variable(
            len(np.concatenate([st.get_opt_voxels_idx(org) for org in df_dvh_criteria.structure_name.to_list()])),
            boolean=True)

        start = 0
        constraints = []
        for i in range(len(df_dvh_criteria)):
            struct, limit, v, M = df_dvh_criteria.loc[i, 'structure_name'], df_dvh_criteria.loc[i, 'dose_gy'], \
                                  df_dvh_criteria.loc[i, 'vol_perc'], df_dvh_criteria.loc[i, 'M']
            end = start + len(st.get_opt_voxels_idx(struct))
            frac = self.my_plan.structures.get_fraction_of_vol_in_calc_box(struct)
            constraints += [
                A[st.get_opt_voxels_idx(struct), :] @ x <= limit / self.my_plan.get_num_of_fractions()
                + b_dvh[start:end] * M / self.my_plan.get_num_of_fractions()]
            constraints += [b_dvh @ st.get_opt_voxels_volume_cc(struct) <= (v / frac) / 100 * sum(
                st.get_opt_voxels_volume_cc(struct))]
            start = end
        self.add_constraints(constraints=constraints)

    @staticmethod
    def get_smoothness_matrix(beamReq: List[dict]) -> (np.ndarray, np.ndarray, int, int):
        """
        Create smoothness matrix so that adjacent beamlets are smooth out to reduce MU

        :param beamReq: beamlets dictionary from the object of influence matrix class
        :returns: tuple(Qx, Qy) where
            Qx: first matrix have values 1 and -1 for neighbouring beamlets in X direction
            Qy: second matrix with values 1 and -1 for neighbouring beamlets in Y direction

        :Example:
        Qx = [[1 -1 0 0 0 0]
              [0 0 1 -1 0 0]
              [0 0 0 0 1 -1]]

        """
        sRow = np.zeros((beamReq[-1]['end_beamlet_idx'] + 1, beamReq[-1]['end_beamlet_idx'] + 1), dtype=int)
        sCol = np.zeros((beamReq[-1]['end_beamlet_idx'] + 1, beamReq[-1]['end_beamlet_idx'] + 1), dtype=int)
        num_rows = 0
        num_cols = 0
        for b in range(len(beamReq)):
            beam_map = beamReq[b]['beamlet_idx_2d_finest_grid']

            rowsNoRepeat = [0]
            for i in range(1, np.size(beam_map, 0)):
                if (beam_map[i, :] != beam_map[rowsNoRepeat[-1], :]).any():
                    rowsNoRepeat.append(i)
            colsNoRepeat = [0]
            for j in range(1, np.size(beam_map, 1)):
                if (beam_map[:, j] != beam_map[:, colsNoRepeat[-1]]).any():
                    colsNoRepeat.append(j)
            beam_map = beam_map[np.ix_(np.asarray(rowsNoRepeat), np.asarray(colsNoRepeat))]
            num_rows = num_rows + beam_map.shape[0]
            num_cols = num_cols + beam_map.shape[1]
            for r in range(np.size(beam_map, 0)):
                startCol = 0
                endCol = np.size(beam_map, 1) - 2
                while (beam_map[r, startCol] == -1) and (startCol <= endCol):
                    startCol = startCol + 1
                while (beam_map[r, endCol] == -1) and (startCol <= endCol):
                    endCol = endCol - 1

                for c in range(startCol, endCol + 1):
                    ind = beam_map[r, c]
                    RN = beam_map[r, c + 1]
                    if ind * RN >= 0:
                        sRow[ind, ind] = int(1)
                        sRow[ind, RN] = int(-1)

            for c in range(np.size(beam_map, 1)):
                startRow = 0
                endRow = np.size(beam_map, 0) - 2
                while (beam_map[startRow, c] == -1) and (startRow <= endRow):
                    startRow = startRow + 1
                while (beam_map[endRow, c] == -1) and (startRow <= endRow):
                    endRow = endRow - 1
                for r in range(startRow, endRow + 1):
                    ind = beam_map[r, c]
                    DN = beam_map[r + 1, c]
                    if ind * DN >= 0:
                        sCol[ind, ind] = int(1)
                        sCol[ind, DN] = int(-1)
        return sRow, sCol, num_rows, num_cols

    @staticmethod
    def matching_keys(dictionary, search_string):
        get_key = None
        for key, val in dictionary.items():
            if search_string in key:
                get_key = key
        if get_key is not None:
            return get_key
        else:
            return ''

    def get_num(self, string: Union[str, float]):
        if "prescription_gy" in string:
            prescription_gy = self.prescription_gy
            return eval(string)
        elif isinstance(string, float) or isinstance(string, int):
            return string
