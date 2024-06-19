#pragma once

#include <QWidget>
#include <QVBoxLayout>
#include <QLabel>
#include <QTimer>
#include <QPainter>

#include "cereal/messaging/messaging.h"
#include "selfdrive/ui/ui.h"

struct AlertProperties {
  QColor borderColor;
  QColor fillColor;
  QColor circleColors[7];
  QColor iconColor;
  QColor textColor;
  int borderWidth;
  QColor shadowColor;
  float shadowOpacity;
};

class DriverAlertCluster : public QWidget {
  Q_OBJECT

public:
  explicit DriverAlertCluster(UIState* ui_State, QWidget* parent = nullptr);
  void updateAlertLevel(const UIState &s);
  cereal::ModelDataV2::DisengagePredictions::Reader getDisengagePredictions(const SubMaster &sm) const;

protected:
  void paintEvent(QPaintEvent* event) override;
  int calculateAlertLevel(const capnp::List<float>::Reader &probs);
  AlertProperties getAlertProperties(int alertLevel);
  void drawAlertBar(QPainter &painter, QLabel *label, const capnp::List<float>::Reader &probs, int yOffset);

private:
  QVBoxLayout *mainLayout;
  QLabel *steeringLabel;
  QLabel *brakeLabel;
  QLabel *gasLabel;
  QTimer *updateTimer;

  int steeringAlertLevel;
  int brakeAlertLevel;
  int gasAlertLevel;

  UIState* ui_State;
};