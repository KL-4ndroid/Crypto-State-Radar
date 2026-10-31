"""Compact research projection, kept separate from the production Dashboard."""


def summary(result,debug=False):
    fields=('symbol','phase','trade_state','distribution_score','squeeze_risk',
            'dynamic_quality','execution_quality','score_breadth','expires_at',
            'executable','signal_eligible','journal_namespace','rule_version','model_status')
    view={name:result[name] for name in fields}
    if debug:
        view.update(families=result['families'],active_blocks=result['active_blocks'],
                    evaluation_key=result['evaluation_key'],feature_versions=result['feature_versions'])
    return view
