import { createAction } from 'redux-actions';
import 'whatwg-fetch';

import { getConfig, postConfig } from '../utils/fetch_configs';
import { getAdmin } from './adminTables';
import { getAdminCounts } from './smart';
import { getLabelCounts } from './skew';

export const SET_HIST_DATA = 'SET_HIST_DATA';
export const SET_UNLABELED_DATA = 'SET_UNLABELED_DATA';

export const set_hist_data = createAction(SET_HIST_DATA);
export const set_unlabeled_data = createAction(SET_UNLABELED_DATA);

//Get the data for the history table
export const getHistory = (projectID) => {
    let apiURL = `/api/get_label_history/${projectID}/`;
    return dispatch => {
        return fetch(apiURL, getConfig())
            .then(response => {
                if (response.ok) {
                    return response.json();
                } else {
                    const error = new Error(response.statusText);
                    error.response = response;
                    throw error;
                }
            })
            .then(response => {
                // If error was in the response then set that message
                let all_data = [];
                for (let i = 0; i < response.data.length; i++) {
                    const row = {
                        id: response.data[i].id,
                        data: response.data[i].data,
                        metadata: response.data[i].metadata,
                        formattedMetadata: response.data[i].formattedMetadata,
                        metadataIDs: response.data[i].metadataIDs,
                        old_label: response.data[i].label,
                        old_label_id: response.data[i].labelID,
                        timestamp: response.data[i].timestamp,
                        edit: response.data[i].edit,
                        project: projectID,
                        profile: response.data[i].profile
                    };
                    all_data.push(row);
                }
                dispatch(set_hist_data(all_data));

                let unlabeled_data = [];
                for (let i = 0; i < response.unlabeled.length; i++) {
                    const row = {
                        id: response.unlabeled[i].id,
                        data: response.unlabeled[i].data,
                        metadata: response.unlabeled[i].metadata,
                        formattedMetadata: response.unlabeled[i].formattedMetadata,
                        metadataIDs: response.unlabeled[i].metadataIDs,
                        edit: response.unlabeled[i].edit,
                        project: projectID,
                    };
                    unlabeled_data.push(row);
                }
                dispatch(set_unlabeled_data(unlabeled_data));
            })
            .catch(err => console.log("Error: ", err));
    };
};


export const changeLabel = (dataID, oldLabelID, labelID, projectID) => {
    let payload = {
        dataID: dataID,
        oldLabelID: oldLabelID,
        labelID: labelID
    };
    let apiURL = `/api/modify_label/${dataID}/`;
    return dispatch => {
        return fetch(apiURL, postConfig(payload))
            .then(response => {
                if (response.ok) {
                    return response.json();
                } else {
                    const error = new Error(response.statusText);
                    error.response = response;
                    throw error;
                }
            })
            .then(() => {
                dispatch(getHistory(projectID));
                dispatch(getLabelCounts(projectID));
            });
    };
};

export const changeToSkip = (dataID, oldLabelID, projectID, message) => {
    let payload = {
        dataID: dataID,
        message,
        oldLabelID: oldLabelID,
    };
    let apiURL = `/api/modify_label_to_skip/${dataID}/`;
    return dispatch => {
        return fetch(apiURL, postConfig(payload))
            .then(response => {
                if (response.ok) {
                    return response.json();
                } else {
                    const error = new Error(response.statusText);
                    error.response = response;
                    throw error;
                }
            })
            .then(() => {
                dispatch(getHistory(projectID));
                dispatch(getAdmin(projectID));
                dispatch(getAdminCounts(projectID));
            });
    };
};

export const modifyMetadataValue = (metadataId, value) => {
    let apiURL = `/api/modify_metadata_value/${metadataId}/`;
    return dispatch => {
        return fetch(apiURL, postConfig({ value }))
            .then(response => {
                if (response.ok) {
                    return response.json();
                } else {
                    const error = new Error(response.statusText);
                    error.response = response;
                    throw error;
                }
            });
    };
};
