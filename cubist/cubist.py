import zlib

import numpy as np
import pandas as pd

from sklearn.utils.validation import check_array, check_is_fitted, \
    check_X_y, check_is_fitted, check_random_state, _check_sample_weight
from sklearn.base import RegressorMixin, BaseEstimator

from ._make_names_string import make_names_string
from ._make_data_string import make_data_string, validate_x
from ._parse_model import parse_model
from ._variable_usage import get_variable_usage
from _cubist import _cubist, _predictions


class Cubist(BaseEstimator, RegressorMixin):
    """
    Cubist Regression Model (Public v2.07) developed by Quinlan.

    References:
    - https://www.rdocumentation.org/packages/Cubist/versions/0.3.0
    - https://www.rulequest.com/cubist-unix.html

    Parameters
    ----------
    n_rules : int, default=500
        Limit of the number of rules Cubist will build. Recommended value is 
        500.

    n_committees : int, default=1
        Number of committees to construct. Each committee is a rule based model 
        and beyond the first tries to correct the prediction errors of the prior 
        constructed model. Recommended value is 5.

    neighbors : int, default=1
        Number between 1 and 9 for how many instances should be used to correct 
        the rule-based prediction.

    unbiased : bool, default=False
        Should unbiased rules be used? Since Cubist minimizes the MAE of the 
        predicted values, the rules may be biased and the mean predicted value 
        may differ from the actual mean. This is recommended when there are 
        frequent occurrences of the same value in a training dataset. Note that 
        MAE may be slightly higher.

    extrapolation : float, default=0.05
        Adjusts how much rule predictions are adjusted to be consistent with 
        the training dataset. Recommended value is 5% as a decimal (0.05)

    sample : float, default=0.0
        Percentage of the data set to be randomly selected for model building.

    random_state : int, default=randint(0, 4095)
        An integer to set the random seed for the C Cubist code.

    target_label : str, default="outcome"
        A label for the outcome variable. This is only used for printing rules.

    verbose : int, default=0
        Should the Cubist output be printed?

    Attributes
    ----------
    names_string_ : str
        String for the Cubist model that describes the training dataset column 
        names and their data types. This also provides some Python environment 
        information.
    
    data_string_ : str
        String containing the training data. Required for using instance-based
        corrections and compressed after model training.

    model_ : str
        The Cubist model string generated by the C code.

    maxd_ : float
        Distance between instances.

    feature_importances_ : pd.DataFrame
        Table of how training data variables are used in the Cubist model. The 
        first column for "Conditions" shows the approximate percentage of cases 
        for which the named attribute appears in a condition of an applicable 
        rule, while the second column "Attributes" gives the percentage of cases 
        for which the attribute appears in the linear formula of an applicable 
        rule.

    rules_ : pd.DataFrame
        Table of the rules built by the Cubist model.

    coeff_ : pd.DataFrame
        Table of the regression coefficients found by the Cubist model.

    variables_ : dict
        Information about all the variables passed to the model and those that 
        were actually used.

    Examples
    --------
    >>> from cubist import Cubist
    >>> from sklearn.datasets import load_boston
    >>> from sklearn.model_selection import train_test_split
    >>> X, y = load_boston(return_X_y=True)
    >>> X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    >>> model = Cubist()
    >>> model.fit(X_train, y_train)
    >>> model.predict(X_test)
    >>> model.score(X_test, y_test)
    """

    def __init__(self,
                 n_rules: int = 500,
                 *,
                 n_committees: int = 1,
                 neighbors: int = 1,
                 unbiased: bool = False,
                 extrapolation: float = 0.05,
                 sample: float = 0.0,
                 random_state: int = None,
                 target_label: str = "outcome",
                 verbose: int = 0):
        super().__init__()

        self.n_rules = n_rules
        self.n_committees = n_committees
        self.neighbors = neighbors
        self.unbiased = unbiased
        self.extrapolation = extrapolation
        self.sample = sample
        self.random_state = random_state
        self.target_label = target_label
        self.verbose = verbose
    
    def _more_tags(self):
        return {"allow_nan": True,
                "X_types": ["2darray", "string"]}

    def fit(self, X, y, sample_weight = None):
        """
        Build a Cubist regression model from training set (X, y).

        Parameters
        ----------
        X : {array-like} of shape (n_samples, n_features)
            The training input samples.

        y : array-like of shape (n_samples,)
            The target values (Real numbers in regression).

        sample_weight : array-like of shape (n_samples,)
            Optional vector of sample weights that is the same length as y for 
            how much each instance should contribute to the model fit.

        Returns
        -------
        self : object
        """
        # validate model parameters
        if self.n_rules < 1 or self.n_rules > 1000000:
            raise ValueError("number of rules must be between 1 and 1000000")

        if self.n_committees < 1 or self.n_committees > 100:
            raise ValueError("number of committees must be between 1 and 100")
        
        if not isinstance(self.neighbors, int):
            raise ValueError("Only an integer value for neighbors is allowed")
        if self.neighbors < 1 or self.neighbors > 9:
            raise ValueError("'neighbors' must be between and including 1 and 9")

        if self.extrapolation < 0.0 or self.extrapolation > 1.0:
            raise ValueError("extrapolation percentage must be between 0.0 and 1.0")

        if self.sample < 0.0 or self.sample > 1.0:
            raise ValueError("sampling percentage must be between 0.0 and 1.0")

        self.random_state_ = check_random_state(self.random_state)

        if sample_weight is not None:
            sample_weight = _check_sample_weight(sample_weight, X)
            self.is_sample_weighted_ = True
        else:
            self.is_sample_weighted_ = False

        
        X, y = check_X_y(X, y, 
                         dtype=None,
                         force_all_finite='allow-nan', 
                         y_numeric=True,
                         ensure_min_samples=2)

        # validate target data type for Cubist
        if not isinstance(y, pd.Series):
            y = pd.Series(y)

        # validate input data
        X = validate_x(X)
        X = X.reset_index(drop=True)
        y = y.reset_index(drop=True)

        # report the number of features passed to the fit method
        self.n_features_in_ = X.shape[1]

        # create the names and data strings required for cubist
        self.names_string_ = make_names_string(X, w=sample_weight, 
                                               label=self.target_label)
        self.data_string_ = make_data_string(X, y, w=sample_weight)
        
        # call the C implementation of cubist
        # TODO: implement remaining options
        ### -u	generate unbiased rules
        # -i	definitely use composite models
        # -a	allow the use of composite models
        # -X folds	carry out a cross-validation (recommended value 10)
        self.model_, output = _cubist(namesv_=self.names_string_.encode(),
                                      datav_=self.data_string_.encode(),
                                      unbiased_=self.unbiased,
                                      compositev_=b"yes",
                                      neighbors_=self.neighbors,
                                      committees_=self.n_committees,
                                      sample_=self.sample,
                                      seed_=self.random_state_.randint(0, 4095) % 4096,
                                      rules_=self.n_rules,
                                      extrapolation_=self.extrapolation,
                                      modelv_=b"1",
                                      outputv_=b"1")

        # convert output from raw to strings
        self.model_ = self.model_.decode()
        output = output.decode()

        # replace "__Sample" with "sample" if this is used in the model
        if "\n__Sample" in self.names_string_:
            output = output.replace("__Sample", "sample")
            self.model_ = self.model_.replace("__Sample", "sample")
            # clean model string when using reserved sample name
            self.model_ = self.model_[:self.model_.index("sample")] + \
                          self.model_[self.model_.index("entries"):]

        # raise cubist errors
        if "Error" in output:
            raise Exception(output)

        # print model output if using verbose output
        if self.verbose:
            print(output)
        
        # compress descriptors and training data
        self.names_string_ = zlib.compress(self.names_string_.encode())
        self.data_string_ = zlib.compress(self.data_string_.encode())

        # parse model contents and store useful information
        self.rules_, self.coeff_, self.maxd_ = parse_model(self.model_, X)

        # remove the maxd value from the model string
        initial_string = f'insts=\"1\" nn=\"1\" maxd=\"{int(self.maxd_)}\"'
        replacement_string = 'insts=\"0\"'
        self.model_ = self.model_.replace(initial_string, replacement_string)

        # get the input data variable usage
        self.feature_importances_ = get_variable_usage(output, X)

        # get the names of columns that have no nan values
        is_na_col = ~self.coeff_.isna().any()
        not_na_cols = self.coeff_.columns[is_na_col].tolist()

        # skip the first three since these are always filled
        not_na_cols = not_na_cols[3:]

        # store a dictionary containing all the training dataset columns and 
        # those that were used by the model
        if self.rules_ is not None:
            used_variables = set(self.rules_["variable"]).union(
                set(not_na_cols)
            )
            self.variables_ = {"all": list(X.columns),
                               "used": list(used_variables)}
        
        self.is_fitted_ = True
        return self

    def predict(self, X):
        """
        Predict Cubist regression target for X.

        Parameters
        ----------
        X : {array-like} of shape (n_samples, n_features)
            The input samples.

        Returns
        -------
        y : ndarray of shape (n_samples,)
            The predicted values.
        """
        check_is_fitted(self)

        # validate input data
        X = check_array(X, dtype=None, force_all_finite="allow-nan")
        X = validate_x(X)
        X = X.reset_index(drop=True)

        if self.neighbors > 0:
            initial_string = "insts=\"0\""
            replacement_string = f"insts=\"1\" nn=\"{self.neighbors}\" maxd=\"{int(self.maxd_)}\""
            model = self.model_.replace(initial_string, replacement_string)
        else:
            model = self.model_

        # If there are case weights used during training, the C code will expect 
        # a column of weights in the new data but the values will be ignored.
        if self.is_sample_weighted_:
            X["case_weight_pred"] = np.nan

        # make data string for predictions
        data_string = make_data_string(X)
        
        # get cubist predictions from trained model
        pred, output = _predictions(data_string.encode(),
                                    zlib.decompress(self.names_string_),
                                    zlib.decompress(self.data_string_),
                                    model.encode(),
                                    np.zeros(X.shape[0]),
                                    b"1")

        # TODO: parse and handle errors in output
        if output:
            print(output.decode())
        return pred
