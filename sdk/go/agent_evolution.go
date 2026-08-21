package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// ListExperienceTrajectories lists trajectories that consumed an Experience.
func (c *Client) ListExperienceTrajectories(
	ctx context.Context,
	experienceURI string,
	opts *ExperienceTrajectoryOptions,
) (map[string]any, error) {
	query := url.Values{
		"experience_uri": []string{NormalizeURI(experienceURI)},
	}
	if opts != nil {
		if opts.Limit != nil {
			queryInt(query, "limit", *opts.Limit)
		}
		if opts.Offset != nil {
			queryInt(query, "offset", *opts.Offset)
		}
		setQueryString(query, "start_date", opts.StartDate)
		setQueryString(query, "end_date", opts.EndDate)
	}
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodGet,
		"/api/v1/agent-evolution/experiences/trajectories",
		query,
		nil,
		&result,
	)
	return result, err
}

// GetExperienceOutcomes returns the outcome distribution for an Experience.
func (c *Client) GetExperienceOutcomes(
	ctx context.Context,
	experienceURI string,
	opts *ExperienceOutcomeOptions,
) (map[string]any, error) {
	query := url.Values{
		"experience_uri": []string{NormalizeURI(experienceURI)},
	}
	if opts != nil {
		setQueryString(query, "start_date", opts.StartDate)
		setQueryString(query, "end_date", opts.EndDate)
	}
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodGet,
		"/api/v1/agent-evolution/experiences/outcomes",
		query,
		nil,
		&result,
	)
	return result, err
}
