from ml.regime_xgb import train_xgb

def walk_forward(df, features):
    results = []

    step = 100

    for i in range(200, len(df)-step, step):
        train = df[:i]
        test = df[i:i+step]

        model = train_xgb(train)

        preds = model.predict(test[features])
        acc = (preds == test["regime"].map({"BULL":1,"SIDE":0,"BEAR":-1})).mean()

        results.append(acc)

    return sum(results)/len(results)