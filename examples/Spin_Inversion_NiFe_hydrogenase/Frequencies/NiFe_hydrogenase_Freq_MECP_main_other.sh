#!/bin/bash
#PBS -N NiFe_hydrogenase_Freq_MECP_main_other
#PBS -l nodes=1:ppn=12
#PBS -o NiFe_hydrogenase_Freq_MECP_main_other.pbs.out
#PBS -e NiFe_hydrogenase_Freq_MECP_main_other.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: NiFe_hydrogenase_Freq_MECP_main_other"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca NiFe_hydrogenase_Freq_MECP_main_other.inp > NiFe_hydrogenase_Freq_MECP_main_other.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
