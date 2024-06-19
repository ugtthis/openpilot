#include <QPainter>

#include "selfdrive/ui/qt/widgets/driver_alert_cluster.h"
#include "cereal/messaging/messaging.h"
#include "selfdrive/ui/ui.h"

// Constructor
DriverAlertCluster::DriverAlertCluster(UIState* ui_State, QWidget *parent)
  : QWidget(parent), steeringAlertLevel(0), gasAlertLevel(0), brakeAlertLevel(0), ui_State(ui_State) {

  mainLayout = new QVBoxLayout(this);
  mainLayout->setContentsMargins(32, 24, 32, 24);

  steeringLabel = new QLabel(this);
  steeringLabel->setText("Steering");
  mainLayout->addWidget(steeringLabel);

  brakeLabel = new QLabel(this);
  brakeLabel->setText("Brake");
  mainLayout->addWidget(brakeLabel);

  gasLabel = new QLabel(this);
  gasLabel->setText("Gas");
  mainLayout->addWidget(gasLabel);

  updateTimer = new QTimer(this);
  connect(updateTimer, &QTimer::timeout, this, QOverload<>::of(&DriverAlertCluster::update));
  updateTimer->start(1000 / 20); //Update at 20Hz
}

// Helper function to get disengage predictions
cereal::ModelDataV2::DisengagePredictions::Reader DriverAlertCluster::getDisengagePredictions(const SubMaster &sm) const {
  return sm["modelV2"].getModelV2().getMeta().getDisengagePredictions();
}

void DriverAlertCluster::updateAlertLevel(const UIState &s) {
  const auto& disengagePreds = getDisengagePredictions(*s.sm);
  steeringAlertLevel = calculateAlertLevel(disengagePreds.getSteerOverrideProbs());
  brakeAlertLevel = calculateAlertLevel(disengagePreds.getBrakeDisengageProbs());
  gasAlertLevel = calculateAlertLevel(disengagePreds.getGasDisengageProbs());
  update();
}

int DriverAlertCluster::calculateAlertLevel(const capnp::List<float>::Reader& probs) {
  float max_prob = *std::max_element(probs.begin(), probs.end());
  int alertLevel = 0;
  if (max_prob > 0.8) alertLevel = 7;
  else if (max_prob > 0.6) alertLevel = 6;
  else if (max_prob > 0.4) alertLevel = 5;
  else if (max_prob > 0.3) alertLevel = 4;
  else if (max_prob > 0.2) alertLevel = 3;
  else if (max_prob > 0.1) alertLevel = 2;
  else if (max_prob > 0.05) alertLevel = 1;

  return alertLevel;
}

AlertProperties DriverAlertCluster::getAlertProperties(int alertLevel) {
  AlertProperties properties;

  switch (alertLevel) {
    case 0: // Disabled
      properties.borderColor = QColor(0, 0, 0, 50);
      properties.fillColor = QColor(11, 16, 22, 50);
      properties.iconColor = QColor(118, 117, 117, 50);
      properties.textColor = QColor(118, 117, 117, 50);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(0, 0, 0, 50);
      properties.shadowOpacity = 0.4f;
      for (int i = 0; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117, 50);
      }
      break;

    case 1: // Low alert level 1
      properties.borderColor = QColor(0, 0, 0);
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(0, 209, 255);
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(0, 0, 0);
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(0, 209, 255);
      for (int i = 1; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117);
      }
      break;

    case 2: // Low alert level 2
      properties.borderColor = QColor(0, 0, 0);
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(0, 209, 255);
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(0, 0, 0);
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(0, 209, 255);
      properties.circleColors[1] = QColor(0, 209, 255);
      for (int i = 2; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117);
      }
      break;

    case 3: // Medium alert level 1
      properties.borderColor = QColor(0, 0, 0);
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(239, 255, 54); // Yellow
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(0, 0, 0);
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[1] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[2] = QColor(239, 255, 54); // Yellow
      for (int i = 3; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117);
      }
      break;

    case 4: // Medium alert level 2
      properties.borderColor = QColor(0, 0, 0);
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(239, 255, 54); // Yellow
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(0, 0, 0);
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[1] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[2] = QColor(239, 255, 54); // Yellow
      properties.circleColors[3] = QColor(239, 255, 54); // Yellow
      for (int i = 4; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117);
      }
      break;

    case 5: // Medium alert level 3
      properties.borderColor = QColor(239, 255, 54); // Yellow
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(254, 255, 255); // White
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(239, 255, 54); // Yellow
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[1] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[2] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[3] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[4] = QColor(239, 255, 54); // Yellow
      for (int i = 5; i < 7; ++i) {
        properties.circleColors[i] = QColor(118, 117, 117);
      }
      break;

    case 6: // High alert level 1
      properties.borderColor = QColor(255, 60, 70); // Red
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(254, 255, 255); // White
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(255, 60, 70); // Red
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[1] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[2] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[3] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[4] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[5] = QColor(255, 60, 70); // Red
      properties.circleColors[6] = QColor(118, 117, 117); // Inactive circle color
      break;

    case 7: // High alert level 2
      properties.borderColor = QColor(255, 60, 70); // Red
      properties.fillColor = QColor(11, 16, 22);
      properties.iconColor = QColor(254, 255, 255); // White
      properties.textColor = QColor(254, 255, 255);
      properties.borderWidth = 4;
      properties.shadowColor = QColor(255, 60, 70); // Red
      properties.shadowOpacity = 0.4f;
      properties.circleColors[0] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[1] = QColor(8, 64, 80); // Dark cyan
      properties.circleColors[2] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[3] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[4] = QColor(67, 71, 21); // Dark yellow
      properties.circleColors[5] = QColor(255, 60, 70); // Red
      properties.circleColors[6] = QColor(255, 60, 70); // Red
      break;

    default:
      break;
  }
  return properties;
}

void DriverAlertCluster::drawAlertBar(QPainter &painter, QLabel *label, const capnp::List<float>::Reader& probs, int yOffset) {
  int alertLevel = calculateAlertLevel(probs);
  AlertProperties properties = getAlertProperties(alertLevel);

  QRect barRect(32, yOffset, 521, 82);
  painter.setBrush(properties.fillColor);
  painter.setPen(QPen(properties.borderColor, properties.borderWidth));
  painter.drawRoundRect(barRect, 16, 16);

  painter.setPen(Qt::NoPen);
  painter.setBrush(properties.shadowColor);
  painter.setOpacity(properties.shadowOpacity);
  painter.drawRoundedRect(barRect, 16, 16);

  painter.setPen(properties.textColor);
  painter.setFont(QFont("Inter", 28, QFont::DemiBold));
  painter.drawText(barRect.adjusted(40, 0, 0, 0), Qt::AlignVCenter, label->text());

  int circleXOffset = barRect.x() + 250;
  for (int i = 0; i < 7; ++i) {
    painter.setBrush(properties.circleColors[i]);
    painter.drawEllipse(circleXOffset + (i * 35), barRect.y() + 27, 27, 27);
  }
}

void DriverAlertCluster::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  const auto& disengagePreds = getDisengagePredictions(*ui_State->sm);

  drawAlertBar(painter, steeringLabel, disengagePreds.getSteerOverrideProbs(), 24);
  drawAlertBar(painter, brakeLabel, disengagePreds.getBrakeDisengageProbs(), 106);
  drawAlertBar(painter, gasLabel, disengagePreds.getGasDisengageProbs(), 188);
}